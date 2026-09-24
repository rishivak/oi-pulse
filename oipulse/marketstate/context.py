"""`BuildContext` — the immutable, content-addressable assembly configuration.

`docs/design/04-MARKETSTATE.md` §1 and `02-DATA_MODEL.md` §5.

A MarketState's identity is `(underlying_id, market_time, knowledge_horizon,
build_context_id)`. The last element is what makes two states built by *different logic
or different budgets* distinguishable rather than silently conflated — the failure it
prevents is a research dataset that mixes states assembled under a 5-second spot budget
with states assembled under a 30-second one and reports a single coverage number.

`build_context_id` **subsumes** `builder_version`, `staleness_policy_version` and
`feature_set_version` (AD-21). Nothing carries `builder_version` separately alongside it;
doing so would create two sources of truth for the same fact, which can disagree.

The id is derived from the configuration's content, so it is stable and comparable across
processes and deployments without coordination: two machines running the same code and
the same budgets compute the same id, and a machine running anything else computes a
different one.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

__all__ = ["BuildContext", "configuration_digest"]

#: Bumped when assembly *logic* changes in a way that alters output for identical input.
BUILDER_VERSION = "1.0.0"
#: Which surfaces and aggregates are materialized (`04` §6).
FEATURE_SET_VERSION = "1.0.0"


def configuration_digest(payload: dict[str, Any]) -> str:
    """Stable digest of assembly-affecting configuration.

    Sorted keys and a compact separator, so the same logical configuration always hashes
    the same regardless of how the mapping was constructed. `default=str` keeps
    `Decimal` and `timedelta` values representable without silently coercing them to
    float, which would make two distinct budgets collide.
    """
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class BuildContext:
    """Immutable description of how a state was assembled.

    Construct with `BuildContext.create(...)` rather than by hand: the constructor takes
    the digest as given, while `create` derives it from the configuration so that
    "materially different configuration implies different identity" holds by
    construction rather than by discipline.
    """

    builder_version: str
    staleness_policy_version: str
    feature_set_version: str
    configuration_digest: str

    @property
    def id(self) -> str:
        """Deterministic identity of this assembly configuration.

        Derived from all four fields, so a change to any of them yields a new id. The
        `bc_` prefix makes the value self-describing in logs and API responses, where a
        bare hex digest is easily mistaken for an observation digest.
        """
        return (
            "bc_"
            + configuration_digest(
                {
                    "builder_version": self.builder_version,
                    "staleness_policy_version": self.staleness_policy_version,
                    "feature_set_version": self.feature_set_version,
                    "configuration_digest": self.configuration_digest,
                }
            )[:32]
        )

    @classmethod
    def create(
        cls,
        *,
        staleness_policy_version: str,
        configuration: dict[str, Any],
        builder_version: str = BUILDER_VERSION,
        feature_set_version: str = FEATURE_SET_VERSION,
    ) -> BuildContext:
        """Derive a context from the configuration that actually affects assembly.

        `configuration` must contain every value that can change the output: the
        staleness budgets, the anchor max age, and anything else the builder consults.
        A value omitted here is a value whose change will not produce a new context id,
        which is precisely how two incomparable states come to share an identity.
        """
        return cls(
            builder_version=builder_version,
            staleness_policy_version=staleness_policy_version,
            feature_set_version=feature_set_version,
            configuration_digest=configuration_digest(configuration),
        )

    def describe(self) -> str:
        return (
            f"{self.id} (builder={self.builder_version}, "
            f"staleness={self.staleness_policy_version}, features={self.feature_set_version})"
        )
