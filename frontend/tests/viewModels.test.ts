/**
 * Screen view models.
 *
 * Every screen's logic lives in a pure module so it can be exercised here, under
 * `node --test`, with no packages installed. The components that consume these are
 * thin by construction — the decisions worth testing are all below.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import type { ExpiryDto, OptionLegDto } from "@/lib/api/dto";
import { buildSurface, selectSurface } from "@/lib/terminal/screens/optionSurface";
import {
  SCREEN_FEATURES,
  featureIdentity,
  featureValueRow,
  partitionAvailable,
} from "@/lib/terminal/screens/analytics";
import {
  ACKNOWLEDGEMENT_EFFECT,
  acknowledgementEffect,
  collectContradictions,
  dryRunNotice,
  occurrenceRow,
  signalRow,
  splitEvidence,
} from "@/lib/terminal/screens/signals";

// ------------------------------------------------------------ option surface

const leg = (over: Partial<OptionLegDto>): OptionLegDto => ({
  instrument_id: 1,
  strike: "25000",
  option_type: "CE",
  ltp: "120.5",
  bid: "120.0",
  ask: "121.0",
  volume: 100,
  oi: 5000,
  provider_prev_oi: 4800,
  iv: "14.8",
  delta: "0.52",
  gamma: "0.001",
  theta: "-8.1",
  vega: "12.0",
  quote_observed_at: "2026-03-03T06:12:15Z",
  greeks_observed_at: "2026-03-03T06:12:15Z",
  quote_age_seconds: 1,
  greeks_age_seconds: 1,
  quote_stale: false,
  oi_stale: false,
  greeks_stale: false,
  ...over,
});

const expiry = (legs: OptionLegDto[], over: Partial<ExpiryDto> = {}): ExpiryDto => ({
  expiry_id: 10,
  expiry_date: "2026-03-26",
  coverage_ratio: 0.99,
  missing_leg_count: 0,
  aggregates: {
    total_call_oi: 1000,
    total_put_oi: 1200,
    pcr: "1.20",
    atm_strike: "25000",
  },
  surfaces: { oi_by_strike: {}, iv_by_strike: {}, gamma_by_strike: {} },
  legs,
  ...over,
});

test("a fresh quote does not make stale greeks look fresh", () => {
  const surface = buildSurface(expiry([leg({ greeks_stale: true, quote_stale: false })]));
  const cell = surface.rows[0].call;
  assert.equal(cell?.quoteStale, false);
  assert.equal(cell?.greeksStale, true);
  assert.equal(cell?.ltp.kind === "present" && cell.ltp.stale, false);
  assert.equal(cell?.delta.kind === "present" && cell.delta.stale, true);
});

test("a missing leg field is an explained absence, not a zero", () => {
  const cell = buildSurface(expiry([leg({ oi: null, delta: null })])).rows[0].call;
  assert.equal(cell?.oi.kind, "absent");
  assert.equal(cell?.delta.kind, "absent");
});

test("strikes sort numerically, not lexically", () => {
  const surface = buildSurface(
    expiry([
      leg({ strike: "25000", instrument_id: 1 }),
      leg({ strike: "9000", instrument_id: 2 }),
      leg({ strike: "10000", instrument_id: 3 }),
    ]),
  );
  assert.deepEqual(surface.rows.map((r) => r.strike), ["9000", "10000", "25000"]);
});

test("a strike present on only one side still gets a row", () => {
  const surface = buildSurface(
    expiry([leg({ strike: "24000", option_type: "PE", instrument_id: 7 })]),
  );
  assert.equal(surface.rows.length, 1);
  assert.equal(surface.rows[0].call, null);
  assert.notEqual(surface.rows[0].put, null);
});

test("the ATM strike is marked from the backend's aggregate, not recomputed", () => {
  const surface = buildSurface(
    expiry([leg({ strike: "25000" }), leg({ strike: "25100", instrument_id: 2 })]),
  );
  assert.deepEqual(surface.rows.map((r) => r.isAtm), [true, false]);
});

test("the expiry selector resolves front, next and an explicit date", () => {
  const a = buildSurface(expiry([leg({})], { expiry_id: 1, expiry_date: "2026-03-26" }));
  const b = buildSurface(expiry([leg({})], { expiry_id: 2, expiry_date: "2026-04-30" }));
  assert.equal(selectSurface([a, b], "front")?.expiryId, 1);
  assert.equal(selectSurface([a, b], "next")?.expiryId, 2);
  assert.equal(selectSurface([a, b], "2026-04-30")?.expiryId, 2);
  assert.equal(selectSurface([a, b], null)?.expiryId, 1);
  // Not silently the first one: an unknown expiry is a miss, not a default.
  assert.equal(selectSurface([a, b], "2027-01-01"), null);
});

// ----------------------------------------------------------------- analytics

test("a feature's version is part of its label", () => {
  const identity = featureIdentity({
    identifier: "GEX_BY_STRIKE",
    version: 2,
    definition: "d",
    formula: null,
    units: null,
    scope: "EXPIRY",
    inputs: [],
    depends_on: [],
    parameters: {},
    lookback: null,
    availability_delay: 2,
    sampling_frequency: null,
    normalization: null,
    quality_requirements: {},
    implementation_ref: null,
  });
  assert.equal(identity.label, "GEX_BY_STRIKE@v2");
  assert.equal(identity.availabilityDelaySeconds, 2);
});

test("a feature the registry does not offer is reported, not dropped", () => {
  const { available, missing } = partitionAvailable("volatility", ["ATM_IV", "IV_CHANGE"]);
  assert.deepEqual(available, ["ATM_IV", "IV_CHANGE"]);
  assert.ok(missing.includes("IV_RANK"));
  assert.equal(available.length + missing.length, SCREEN_FEATURES.volatility.length);
});

test("a value row surfaces available_at, which raw observations do not have", () => {
  const row = featureValueRow({
    market_time: "T",
    knowledge_time: "K",
    available_at: "A",
    value: 1.5,
  });
  assert.equal(row.availableAt, "A");
  assert.equal(row.value.kind, "present");
});

test("a row with no value is absent rather than zero", () => {
  assert.equal(featureValueRow({}).value.kind, "absent");
});

// ------------------------------------------------------------------- signals

const signal = (over: Record<string, unknown> = {}) =>
  ({
    signal_id: "sig_1",
    type: "PUT_SUPPORT_MIGRATION",
    rule_version: 2,
    underlying_id: 1,
    expiry_id: 10,
    occurrence: 1,
    status: "ACTIVE",
    strength: "0.68",
    horizon_seconds: 1800,
    market_time: "2026-03-03T11:42:00Z",
    knowledge_time: "2026-03-03T11:42:00Z",
    created_at: null,
    updated_at: null,
    available_at: "2026-03-03T11:42:02Z",
    expires_at: "2026-03-03T12:15:00Z",
    invalidation_condition: "support breaks",
    quality_status: "OK",
    contradiction_assessment: "NONE_OBSERVED",
    provenance: {} as never,
    content_digest: "sha",
    ...over,
  }) as never;

test("assessed-no-contradiction is distinguishable from an empty evidence list", () => {
  const none = splitEvidence(signal());
  assert.equal(none.assessedNoContradiction, true);
  assert.equal(none.contradicting, null);

  const some = splitEvidence(
    signal({
      contradiction_assessment: [
        { kind: "CONTRADICTING", statement: "IV expanding", weight: null, metric_ref: null, observation_refs: [], observed_at: null },
      ],
    }),
  );
  assert.equal(some.assessedNoContradiction, false);
  assert.equal(some.contradicting?.length, 1);
});

test("FORMING is rendered as not actionable", () => {
  assert.equal(signalRow(signal({ status: "FORMING" })).actionable, false);
  assert.equal(signalRow(signal({ status: "ACTIVE" })).actionable, true);
});

test("a signal's availability is carried into the row", () => {
  assert.equal(signalRow(signal()).availableAt, "2026-03-03T11:42:02Z");
});

test("the contradictions panel reports the rules' own contradicting evidence", () => {
  const found = collectContradictions([
    signal(),
    signal({
      signal_id: "sig_2",
      contradiction_assessment: [
        {
          kind: "CONTRADICTING",
          statement: "IV expanding while support strengthens",
          weight: null,
          metric_ref: "ATM_IV@1",
          observation_refs: ["obs_9"],
          observed_at: null,
        },
      ],
    }),
  ]);
  assert.equal(found.length, 1);
  assert.equal(found[0].signalId, "sig_2");
  assert.equal(found[0].metricRef, "ATM_IV@1");
});

test("acknowledgement is delivery state and says so", () => {
  const effect = acknowledgementEffect({ signal_unchanged: true });
  assert.equal(effect.signalUnchanged, true);
  assert.match(ACKNOWLEDGEMENT_EFFECT, /delivery state only/);
  // Not assumed: a response that stopped saying so stops being claimed.
  assert.equal(acknowledgementEffect({}).signalUnchanged, false);
});

test("a dry run is only called one when nothing was persisted or delivered", () => {
  assert.match(dryRunNotice({ dry_run: true, persisted: false, delivered: false }), /Dry run/);
  assert.match(dryRunNotice({ persisted: true, delivered: false }), /It was not a dry run/);
});

test("delivery state distinguishes pending, failed and acknowledged", () => {
  const base = {
    occurrence_id: "o1",
    rule_id: "r1",
    rule_config_digest: "d",
    signal_id: "sig_1",
    signal_type: "T",
    underlying_id: 1,
    status: "TRIGGERED",
    severity: "INFO",
    channel: "LOG",
    observed_at: null,
    available_at: null,
    triggered_at: null,
    dedup_key: "k",
    delivered: false,
    attempts: [],
    acknowledged_at: null,
    acknowledged_by: null,
  };
  assert.equal(occurrenceRow(base).deliveryState, "PENDING");
  assert.equal(occurrenceRow({ ...base, delivered: true }).deliveryState, "DELIVERED");
  assert.equal(
    occurrenceRow({
      ...base,
      attempts: [{ attempt: 1, attempted_at: null, status: "FAILED", channel: "LOG", detail: null }],
    }).deliveryState,
    "FAILED",
  );
  assert.equal(
    occurrenceRow({ ...base, delivered: true, acknowledged_at: "t" }).deliveryState,
    "ACKNOWLEDGED",
  );
});
