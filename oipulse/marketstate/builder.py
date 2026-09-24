"""`build_state` — the single deterministic MarketState construction path.

`docs/design/04-MARKETSTATE.md` §4 (assembly algorithm) and §5.

> **The same function serves live assembly, reconstruction, replay and backtest.**

That single-implementation rule is the point of this module. A separate "historical"
path is the classic source of backtest/live mismatch: the two drift, and the drift is
invisible until a strategy that backtested well loses money. `StateBuilder.build` is the
only way a `MarketState` is ever produced, including when materializing or validating a
checkpoint.

**Determinism requirements, each enforced here rather than trusted:**

* No wall-clock read. The only clock use is `assembled_at` in provenance, which is
  excluded from the content digest. Every `age` is `market_time - observed_at`.
* No future information. Every read goes through one temporal bound derived from
  `(T, K)`; there is no second, unbounded query path.
* No mutable global state. The builder holds its collaborators and nothing else.
* No nondeterministic iteration. Every collection is explicitly sorted before use, so
  the order observations arrived in cannot change the result.

**Point-in-time rule.** An observation participates only when `observed_at <= T` **and**
`ingested_at <= K`. The worked example from the brief: an observation with
`observed_at = 11:40, ingested_at = 11:44` must not appear in `MarketState(T=11:45,
K=11:42)`, and may appear in `MarketState(T=11:45, K=11:45)`. `observed_at` alone never
establishes participation.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, time, timedelta
from decimal import Decimal
from types import MappingProxyType
from typing import Protocol

from oipulse.core.clock import Clock
from oipulse.core.errors import OIPulseError
from oipulse.core.ids import ExpiryId, InstrumentId
from oipulse.core.timemode import KnowledgeAt, MarketTruthAt, TemporalBound
from oipulse.dataquality.issues import IssueSeverity, IssueType, QualityIssue
from oipulse.instruments.models import Instrument, OptionType
from oipulse.marketdata.observations import (
    GreeksObservation,
    IndexObservation,
    MarketObservation,
    ObservationKind,
    QuoteObservation,
)
from oipulse.marketstate.context import BuildContext
from oipulse.marketstate.staleness import (
    ANCHOR_MAX_AGE_MULTIPLIER,
    DEFAULT_STALENESS_POLICY,
    DataCategory,
    StalenessPolicy,
)
from oipulse.marketstate.state import (
    Coherence,
    CoherenceMode,
    ExpiryAggregates,
    ExpirySlice,
    FuturesLeg,
    MarketState,
    OptionLeg,
    Provenance,
    Quality,
    SessionPhase,
    SpotView,
    StateIdentity,
    Surfaces,
)
from oipulse.marketstate.universe import ResolvedUniverse, UniverseResolver

__all__ = [
    "DEFAULT_ANCHOR_MAX_AGE",
    "AnchorSource",
    "ChainAnchor",
    "IncoherentTimeRange",
    "ObservationSource",
    "StateBuilder",
]

#: 2x the chain poll interval (`04` §4). The poll interval is deployment configuration;
#: 15 s is the Phase 2 default cadence, so the anchor budget is 30 s.
DEFAULT_CHAIN_POLL_INTERVAL = timedelta(seconds=15)
DEFAULT_ANCHOR_MAX_AGE = DEFAULT_CHAIN_POLL_INTERVAL * ANCHOR_MAX_AGE_MULTIPLIER

_NSE_OPEN = time(9, 15)
_NSE_CLOSE = time(15, 30)
_IST_OFFSET_MINUTES = 330


class IncoherentTimeRange(OIPulseError):
    """`knowledge_time < market_time` on an API request — rejected (`12-API_SPEC.md` §2).

    **Raised at the API boundary, not by the builder**, and the distinction is
    deliberate. Two design documents speak to `K < T` and they are not in conflict once
    the layer is taken into account:

    * `12-API_SPEC.md` §2 rejects `knowledge_time < market_time` on `/market/state`. At
      the HTTP edge the combination is almost always a mistake -- a client filling in
      two timestamps the wrong way round -- and answering it would silently return a
      much emptier state than the caller expected.
    * `04-MARKETSTATE.md` §1 and the point-in-time rule require the *builder* to honour
      any `(T, K)`: `observed_at <= T AND ingested_at <= K` is well defined for every
      ordering, and `MarketState(T=11:45, K=11:42)` is precisely the query that proves
      a late-arriving observation is excluded.

    So the mechanism is general and the endpoint is strict. A replay harness or a
    research query may build with `K < T` directly; an HTTP client must not stumble
    into it.
    """


class ObservationSource(Protocol):
    """The read surface the builder needs.

    Structural, so the in-memory twin used offline and the PostgreSQL repository satisfy
    one contract and the builder cannot acquire a second, unbounded query path.
    """

    def latest(
        self, bound: TemporalBound, instrument_id: int, kind: ObservationKind
    ) -> MarketObservation | None: ...


class ChainAnchor(Protocol):
    """A REST chain snapshot: the venue's own cross-sectional view (`04` §4)."""

    @property
    def reference(self) -> str: ...
    @property
    def expiry_id(self) -> ExpiryId: ...
    @property
    def observed_at(self) -> datetime: ...
    @property
    def legs(self) -> Sequence[MarketObservation]: ...


class AnchorSource(Protocol):
    """Supplies the latest chain snapshot visible under a bound."""

    def latest_anchor(
        self, underlying_id: InstrumentId, expiry_id: ExpiryId, bound: TemporalBound
    ) -> ChainAnchor | None: ...


def _session_phase(at: datetime) -> SessionPhase:
    """NSE session phase for a market time. Pure function of `T`, never of now."""
    minutes = at.hour * 60 + at.minute + _IST_OFFSET_MINUTES
    ist = minutes % (24 * 60)
    weekday = (at.weekday() + minutes // (24 * 60)) % 7
    if weekday >= 5:
        return SessionPhase.CLOSED
    open_at = _NSE_OPEN.hour * 60 + _NSE_OPEN.minute
    close_at = _NSE_CLOSE.hour * 60 + _NSE_CLOSE.minute
    if ist < open_at - 15:
        return SessionPhase.CLOSED
    if ist < open_at:
        return SessionPhase.PRE_OPEN
    if ist < close_at:
        return SessionPhase.OPEN
    if ist < close_at + 30:
        return SessionPhase.POST_CLOSE
    return SessionPhase.CLOSED


class StateBuilder:
    """Assembles `MarketState`. One instance per configuration, reusable and stateless.

    Stateless between calls on purpose: any retained state would be mutable global
    state by another name and could make two identical requests return different
    answers.
    """

    def __init__(
        self,
        observations: ObservationSource,
        universe: UniverseResolver,
        clock: Clock,
        *,
        policy: StalenessPolicy = DEFAULT_STALENESS_POLICY,
        anchors: AnchorSource | None = None,
        anchor_max_age: timedelta = DEFAULT_ANCHOR_MAX_AGE,
        build_context: BuildContext | None = None,
    ) -> None:
        self._observations = observations
        self._universe = universe
        self._clock = clock
        self._policy = policy
        self._anchors = anchors
        self._anchor_max_age = anchor_max_age
        self._context = build_context or BuildContext.create(
            staleness_policy_version=policy.version,
            configuration={
                **policy.as_configuration(),
                "anchor_max_age_seconds": anchor_max_age.total_seconds(),
            },
        )

    @property
    def build_context(self) -> BuildContext:
        return self._context

    @property
    def policy(self) -> StalenessPolicy:
        return self._policy

    # ------------------------------------------------------------------ bound

    @staticmethod
    def bound_for(market_time: datetime, knowledge_horizon: datetime) -> TemporalBound:
        """The single temporal bound every read in a build goes through.

        Three cases, and the third is the subtle one:

        * `K == T` -> `knowledge_at(T)`.
        * `K > T`  -> `market_truth_at(valid_time=T, knowledge_as_of=K)`. Explicit
          opt-in market-truth: what was true at T, per everything known by K.
        * `K < T`  -> `knowledge_at(K)`.

        The last case needs justifying, because `market_truth_at` refuses it: Phase 1's
        `resolve_bound` rejects `knowledge_as_of < valid_time` as "asking what we knew
        about the future", matching `10-REPLAY.md` §2. That decision stands.

        It does not block the request, because the knowledge constraint *dominates*
        when `K < T`. An observation cannot be ingested before it was observed, so
        `ingested_at <= K` already implies `observed_at <= K < T`, and the pair
        `(observed_at <= T, ingested_at <= K)` collapses to `knowledge_at(K)`. This is
        exactly the brief's `MarketState(T=11:45, K=11:42)`: the 11:40/11:44
        observation is excluded because it had not been ingested, which is the point
        being made.

        The one input where the two readings differ is a row with recorded clock skew,
        where `observed_at > ingested_at`. There `knowledge_at(K)` is the strictly
        narrower answer -- it excludes a row that the literal pair would admit -- and
        the narrower answer is the right default: it cannot manufacture look-ahead,
        and the skew is already recorded as a `CLOCK_SKEW` quality issue rather than
        being silently normalised away.

        Producing the bound in exactly one place is what makes it impossible for one
        component of a state to be read under a different horizon than another. A state
        assembled from two horizons is not a point-in-time observation of anything, and
        the defect would be invisible in the output.
        """
        if knowledge_horizon == market_time:
            return KnowledgeAt(market_time)
        if knowledge_horizon < market_time:
            return KnowledgeAt(knowledge_horizon)
        return MarketTruthAt(valid_time=market_time, knowledge_as_of=knowledge_horizon)

    # ------------------------------------------------------------------ build

    def build(
        self,
        underlying_id: InstrumentId,
        market_time: datetime,
        knowledge_horizon: datetime | None = None,
    ) -> MarketState:
        """Assemble the state at `(T, K)`. `K` defaults to `T` (`knowledge_at`)."""
        k = knowledge_horizon if knowledge_horizon is not None else market_time
        bound = self.bound_for(market_time, k)
        identity = StateIdentity(
            underlying_id=underlying_id,
            market_time=market_time,
            knowledge_horizon=k,
            build_context_id=self._context.id,
        )

        universe = self._universe.resolve(underlying_id, market_time)
        issues: list[QualityIssue] = []
        refs: list[str] = []
        ages: list[timedelta] = []
        breached: set[DataCategory] = set()

        spot = self._build_spot(universe, market_time, bound, issues, refs, ages, breached)
        futures = self._build_futures(universe, market_time, bound, issues, refs, ages, breached)
        expiries, merge_count, anchor_at, anchor_ref, anchored_all = self._build_expiries(
            universe, market_time, bound, issues, refs, ages, breached
        )

        coverage = _coverage(expiries)
        missing_expiry = self._missing_subscribed_expiry(universe, expiries)
        if missing_expiry:
            issues.append(
                QualityIssue(
                    type=IssueType.INCOMPLETE_CHAIN,
                    severity=IssueSeverity.CRITICAL,
                    detected_at=market_time,
                    underlying_id=int(underlying_id),
                    detail="no chain data for a subscribed expiry",
                )
            )

        status = self._policy.escalate(
            breached=frozenset(breached),
            coverage_ratio=coverage,
            missing_subscribed_expiry=missing_expiry,
        )
        mode = self._coherence_mode(anchor_at, market_time, anchored_all, issues)

        return MarketState(
            identity=identity,
            session_phase=_session_phase(market_time),
            session_date=market_time.date(),
            spot=spot,
            futures=futures,
            expiries=expiries,
            coherence=Coherence(
                mode=mode,
                max_component_age=max(ages) if ages else None,
                chain_snapshot_ref=anchor_ref,
                ws_merge_count=merge_count,
                anchor_observed_at=anchor_at,
            ),
            quality=Quality(
                status=status,
                coverage_ratio=coverage,
                staleness_p95=_p95(ages),
                issues=tuple(issues),
            ),
            provenance=Provenance(
                build_context=self._context,
                observation_refs=tuple(sorted(refs)),
                assembled_at=self._clock.now(),
                source_kinds=tuple(sorted({"observation"})),
            ),
        )

    # ------------------------------------------------------------- components

    def _age(self, market_time: datetime, observed_at: datetime | None) -> timedelta | None:
        """`T - observed_at`. Never `now - observed_at`: a state reconstructed for last
        March must report the ages that held in March, not ages measured from today."""
        return None if observed_at is None else market_time - observed_at

    def _record(
        self,
        obs: MarketObservation | None,
        refs: list[str],
    ) -> None:
        if obs is not None:
            refs.append(f"{obs.kind.value}:{int(obs.instrument_id)}:{obs.observed_at.isoformat()}")

    def _flag_stale(
        self,
        category: DataCategory,
        age: timedelta | None,
        market_time: datetime,
        instrument_id: int | None,
        issues: list[QualityIssue],
        breached: set[DataCategory],
    ) -> bool:
        if age is None or not self._policy.is_stale(category, age):
            return False
        budget = self._policy.budget_for(category)
        issues.append(
            QualityIssue(
                type=IssueType.STALE_PRICE,
                severity=budget.severity,
                detected_at=market_time,
                instrument_id=instrument_id,
                detail=(
                    f"{category.value} age {age.total_seconds():.1f}s exceeds "
                    f"{budget.max_age.total_seconds():.0f}s budget"
                ),
            )
        )
        # A dropped category leaves no stale value in the state, so it does not
        # escalate the state's status (`staleness.escalate`).
        if not budget.drop_on_breach:
            breached.add(category)
        return True

    def _build_spot(
        self,
        universe: ResolvedUniverse,
        market_time: datetime,
        bound: TemporalBound,
        issues: list[QualityIssue],
        refs: list[str],
        ages: list[timedelta],
        breached: set[DataCategory],
    ) -> SpotView:
        if universe.spot_instrument_id is None:
            issues.append(
                QualityIssue(
                    type=IssueType.MISSING_OBSERVATION,
                    severity=IssueSeverity.CRITICAL,
                    detected_at=market_time,
                    underlying_id=int(universe.underlying_id),
                    detail="no spot instrument in the resolved universe",
                )
            )
            breached.add(DataCategory.SPOT)
            return SpotView(ltp=None, observed_at=None, age=None)

        obs = self._observations.latest(
            bound, int(universe.spot_instrument_id), ObservationKind.INDEX
        )
        if obs is None:
            issues.append(
                QualityIssue(
                    type=IssueType.MISSING_OBSERVATION,
                    severity=IssueSeverity.CRITICAL,
                    detected_at=market_time,
                    instrument_id=int(universe.spot_instrument_id),
                    detail="no spot observation visible at this knowledge horizon",
                )
            )
            breached.add(DataCategory.SPOT)
            return SpotView(ltp=None, observed_at=None, age=None)

        self._record(obs, refs)
        age = self._age(market_time, obs.observed_at)
        if age is not None:
            ages.append(age)
        stale = self._flag_stale(
            DataCategory.SPOT, age, market_time, int(obs.instrument_id), issues, breached
        )
        index = obs if isinstance(obs, IndexObservation) else None
        return SpotView(
            ltp=index.ltp if index else None,
            observed_at=obs.observed_at,
            age=age,
            prev_close=index.prev_close if index else None,
            open=index.open if index else None,
            high=getattr(index, "high", None),
            low=getattr(index, "low", None),
            stale=stale,
        )

    def _build_futures(
        self,
        universe: ResolvedUniverse,
        market_time: datetime,
        bound: TemporalBound,
        issues: list[QualityIssue],
        refs: list[str],
        ages: list[timedelta],
        breached: set[DataCategory],
    ) -> tuple[FuturesLeg, ...]:
        out: list[FuturesLeg] = []
        for contract in sorted(universe.futures, key=lambda i: int(i.id)):
            obs = self._observations.latest(bound, int(contract.id), ObservationKind.QUOTE)
            quote = obs if isinstance(obs, QuoteObservation) else None
            age = self._age(market_time, obs.observed_at if obs else None)
            if age is not None:
                ages.append(age)
            self._record(obs, refs)
            stale = self._flag_stale(
                DataCategory.FUTURES, age, market_time, int(contract.id), issues, breached
            )
            out.append(
                FuturesLeg(
                    instrument_id=contract.id,
                    expiry_id=contract.expiry_id,
                    ltp=quote.ltp if quote else None,
                    oi=quote.oi if quote else None,
                    volume=quote.volume if quote else None,
                    observed_at=obs.observed_at if obs else None,
                    age=age,
                    stale=stale,
                )
            )
        return tuple(out)

    def _build_expiries(
        self,
        universe: ResolvedUniverse,
        market_time: datetime,
        bound: TemporalBound,
        issues: list[QualityIssue],
        refs: list[str],
        ages: list[timedelta],
        breached: set[DataCategory],
    ) -> tuple[tuple[ExpirySlice, ...], int, datetime | None, str | None, bool]:
        slices: list[ExpirySlice] = []
        merge_count = 0
        anchor_at: datetime | None = None
        anchor_ref: str | None = None
        anchored_all = bool(universe.expiries)

        for entry in universe.sorted_expiries():
            anchor = (
                self._anchors.latest_anchor(universe.underlying_id, entry.expiry_id, bound)
                if self._anchors is not None
                else None
            )
            if anchor is None:
                anchored_all = False
            else:
                if anchor_at is None or anchor.observed_at > anchor_at:
                    anchor_at = anchor.observed_at
                    anchor_ref = anchor.reference

            legs: list[OptionLeg] = []
            missing = 0
            for contract in sorted(
                entry.legs, key=lambda i: (i.strike or Decimal(0), str(i.option_type), int(i.id))
            ):
                leg = self._build_leg(
                    contract, entry.expiry_id, market_time, bound, issues, refs, ages, breached
                )
                if leg is None:
                    missing += 1
                    continue
                if (
                    anchor is not None
                    and leg.quote_observed_at is not None
                    and leg.quote_observed_at > anchor.observed_at
                ):
                    merge_count += 1
                legs.append(leg)

            if missing:
                issues.append(
                    QualityIssue(
                        type=IssueType.INCOMPLETE_CHAIN,
                        severity=IssueSeverity.DEGRADED,
                        detected_at=market_time,
                        underlying_id=int(universe.underlying_id),
                        detail=f"{missing} leg(s) absent for expiry {int(entry.expiry_id)}",
                    )
                )

            frozen_legs = tuple(legs)
            slices.append(
                ExpirySlice(
                    expiry_id=entry.expiry_id,
                    expiry_date=entry.expiry.expiry_date,
                    legs=frozen_legs,
                    surfaces=_surfaces(frozen_legs),
                    aggregates=_aggregates(frozen_legs, self._spot_hint(universe, bound)),
                    missing_leg_count=missing,
                )
            )
        return tuple(slices), merge_count, anchor_at, anchor_ref, anchored_all

    def _spot_hint(self, universe: ResolvedUniverse, bound: TemporalBound) -> Decimal | None:
        """Spot used only to pick the ATM strike, under the same bound as everything else."""
        if universe.spot_instrument_id is None:
            return None
        obs = self._observations.latest(
            bound, int(universe.spot_instrument_id), ObservationKind.INDEX
        )
        return obs.ltp if isinstance(obs, IndexObservation) else None

    def _build_leg(
        self,
        contract: Instrument,
        expiry_id: ExpiryId,
        market_time: datetime,
        bound: TemporalBound,
        issues: list[QualityIssue],
        refs: list[str],
        ages: list[timedelta],
        breached: set[DataCategory],
    ) -> OptionLeg | None:
        quote_obs = self._observations.latest(bound, int(contract.id), ObservationKind.QUOTE)
        greeks_obs = self._observations.latest(bound, int(contract.id), ObservationKind.GREEKS)
        if quote_obs is None and greeks_obs is None:
            # Absent, not zero. The leg is counted as missing so coverage reflects it.
            return None

        quote = quote_obs if isinstance(quote_obs, QuoteObservation) else None
        greeks = greeks_obs if isinstance(greeks_obs, GreeksObservation) else None
        self._record(quote_obs, refs)
        self._record(greeks_obs, refs)

        quote_age = self._age(market_time, quote_obs.observed_at if quote_obs else None)
        greeks_age = self._age(market_time, greeks_obs.observed_at if greeks_obs else None)
        for age in (quote_age, greeks_age):
            if age is not None:
                ages.append(age)

        quote_stale = self._flag_stale(
            DataCategory.OPTION_QUOTE, quote_age, market_time, int(contract.id), issues, breached
        )
        # OI carries its own, looser budget: it is observed on the same row as the
        # quote but updates at a different cadence, so judging it by the quote budget
        # would flag it stale while it is perfectly current for what it is.
        oi_stale = self._flag_stale(
            DataCategory.OPTION_OI, quote_age, market_time, int(contract.id), issues, breached
        )
        greeks_stale = self._flag_stale(
            DataCategory.GREEKS, greeks_age, market_time, int(contract.id), issues, breached
        )

        return OptionLeg(
            instrument_id=contract.id,
            expiry_id=expiry_id,
            strike=contract.strike if contract.strike is not None else Decimal(0),
            option_type=(contract.option_type or OptionType.CALL).value,
            ltp=quote.ltp if quote else None,
            bid=quote.bid if quote else None,
            ask=quote.ask if quote else None,
            volume=quote.volume if quote else None,
            oi=quote.oi if quote else None,
            provider_prev_oi=quote.provider_prev_oi if quote else None,
            iv=greeks.iv if greeks else None,
            delta=greeks.delta if greeks else None,
            gamma=greeks.gamma if greeks else None,
            theta=greeks.theta if greeks else None,
            vega=greeks.vega if greeks else None,
            quote_observed_at=quote_obs.observed_at if quote_obs else None,
            greeks_observed_at=greeks_obs.observed_at if greeks_obs else None,
            quote_age=quote_age,
            greeks_age=greeks_age,
            quote_stale=quote_stale,
            oi_stale=oi_stale,
            greeks_stale=greeks_stale,
        )

    # -------------------------------------------------------------- coherence

    def _coherence_mode(
        self,
        anchor_at: datetime | None,
        market_time: datetime,
        anchored_all: bool,
        issues: list[QualityIssue],
    ) -> CoherenceMode:
        """Which regime produced the state (`04` §4).

        `RECOVERING` wins over everything: a gap is in flight, so no claim of
        cross-sectional consistency is defensible regardless of anchor age.
        """
        if any(i.type in (IssueType.WEBSOCKET_GAP, IssueType.RECONNECT_GAP) for i in issues):
            return CoherenceMode.RECOVERING
        if anchor_at is None or not anchored_all:
            return CoherenceMode.STREAM_ONLY
        if (market_time - anchor_at) > self._anchor_max_age:
            return CoherenceMode.SNAPSHOT_STALE
        return CoherenceMode.SNAPSHOT_ANCHORED

    def _missing_subscribed_expiry(
        self, universe: ResolvedUniverse, slices: tuple[ExpirySlice, ...]
    ) -> bool:
        if not universe.subscribed_expiry_ids:
            return False
        with_data = {int(s.expiry_id) for s in slices if s.legs}
        return bool(universe.subscribed_expiry_ids - with_data)


# ------------------------------------------------------------------ surfaces


def _surfaces(legs: tuple[OptionLeg, ...]) -> Surfaces:
    """Reorganise legs by strike. No formula is applied (`04` §6).

    Each entry is `(call_value, put_value)`; `None` on either side means no observation,
    which is different from an observed zero and stays different.
    """
    oi: dict[Decimal, tuple[int | None, int | None]] = {}
    iv: dict[Decimal, tuple[Decimal | None, Decimal | None]] = {}
    gamma: dict[Decimal, tuple[Decimal | None, Decimal | None]] = {}

    for leg in sorted(legs, key=lambda leg: (leg.strike, leg.option_type)):
        is_call = leg.option_type == OptionType.CALL.value
        # Written out per surface rather than looped over a heterogeneous tuple: the
        # three tables hold different value types, and a shared loop can only be typed
        # by widening all of them, which would let an IV land in the OI surface.
        oi_call, oi_put = oi.get(leg.strike, (None, None))
        oi[leg.strike] = (leg.oi, oi_put) if is_call else (oi_call, leg.oi)

        iv_call, iv_put = iv.get(leg.strike, (None, None))
        iv[leg.strike] = (leg.iv, iv_put) if is_call else (iv_call, leg.iv)

        g_call, g_put = gamma.get(leg.strike, (None, None))
        gamma[leg.strike] = (leg.gamma, g_put) if is_call else (g_call, leg.gamma)

    return Surfaces(
        oi_by_strike=MappingProxyType(dict(sorted(oi.items()))),
        iv_by_strike=MappingProxyType(dict(sorted(iv.items()))),
        gamma_by_strike=MappingProxyType(dict(sorted(gamma.items()))),
    )


def _aggregates(legs: tuple[OptionLeg, ...], spot: Decimal | None) -> ExpiryAggregates:
    """Sums over observed OI, and the ATM strike.

    A sum of *no* observations is `None`, not `0`: reporting zero total call OI for an
    expiry we simply could not see would be a fabricated fact.
    """
    call_oi = [
        leg.oi for leg in legs if leg.option_type == OptionType.CALL.value and leg.oi is not None
    ]
    put_oi = [
        leg.oi for leg in legs if leg.option_type == OptionType.PUT.value and leg.oi is not None
    ]
    strikes = sorted({leg.strike for leg in legs})
    atm = min(strikes, key=lambda s: abs(s - spot)) if strikes and spot is not None else None
    return ExpiryAggregates(
        total_call_oi=sum(call_oi) if call_oi else None,
        total_put_oi=sum(put_oi) if put_oi else None,
        atm_strike=atm,
    )


def _coverage(slices: tuple[ExpirySlice, ...]) -> float:
    present = sum(len(s.legs) for s in slices)
    expected = present + sum(s.missing_leg_count for s in slices)
    return 1.0 if expected == 0 else present / expected


def _p95(ages: list[timedelta]) -> timedelta | None:
    """Nearest-rank p95. Deterministic for any input order because the list is sorted."""
    if not ages:
        return None
    ordered = sorted(ages)
    index = min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))
    return ordered[index]
