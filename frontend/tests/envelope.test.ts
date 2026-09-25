/**
 * The response envelope. `12-API_SPEC.md` §2.
 *
 * The contract being tested is the one the spec justifies out loud: "A consumer
 * cannot render a number without having been told how reliable it is."
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { MalformedEnvelope, answeredAxes, parseEnvelope } from "@/lib/terminal/envelope";

const FULL = {
  data: { spot: { ltp: "25120.40" } },
  meta: {
    market_time: "2026-03-03T11:42:15Z",
    knowledge_time: "2026-03-03T11:42:15Z",
    semantics: "knowledge_at",
    quality: { status: "OK", coverage_ratio: 0.99, issues: [] },
    coherence_mode: "SNAPSHOT_ANCHORED",
    provenance: {
      build_context_id: "bc_7f3a",
      feature_versions: { GEX_BY_STRIKE: 2 },
      content_digest: "sha256:abc",
      observation_refs: ["obs_1"],
      assembled_at: "2026-03-03T11:42:16Z",
    },
  },
};

test("both axes and the semantics are read back, never one of them", () => {
  const envelope = parseEnvelope<unknown>(FULL);
  assert.equal(envelope.meta.marketTime, "2026-03-03T11:42:15Z");
  assert.equal(envelope.meta.knowledgeTime, "2026-03-03T11:42:15Z");
  assert.equal(envelope.meta.semantics, "knowledge_at");
});

test("quality arrives with the data, not as a separate optional lookup", () => {
  const envelope = parseEnvelope<unknown>(FULL);
  assert.equal(envelope.quality.status, "OK");
  assert.equal(envelope.quality.withholdDerived, false);
});

test("an envelope with no quality yields UNKNOWN and withholds derived values", () => {
  const envelope = parseEnvelope<unknown>({ data: {}, meta: {} });
  assert.equal(envelope.quality.status, "UNKNOWN");
  assert.equal(envelope.quality.withholdDerived, true);
});

test("a body with no `data` key is malformed rather than empty", () => {
  assert.throws(() => parseEnvelope({ meta: {} }), MalformedEnvelope);
});

test("provenance is extracted, including feature versions", () => {
  const { provenance } = parseEnvelope<unknown>(FULL).meta;
  assert.equal(provenance.buildContextId, "bc_7f3a");
  assert.equal(provenance.contentDigest, "sha256:abc");
  assert.deepEqual(provenance.featureVersions, { GEX_BY_STRIKE: 2 });
  assert.deepEqual(provenance.observationRefs, ["obs_1"]);
});

test("quality issues carry severity into the message", () => {
  const envelope = parseEnvelope<unknown>({
    data: {},
    meta: {
      quality: {
        status: "DEGRADED",
        issues: [{ type: "COVERAGE_GAP", severity: "WARNING", detail: "0.4 coverage" }],
      },
    },
  });
  assert.equal(envelope.quality.issues[0].code, "COVERAGE_GAP");
  assert.match(envelope.quality.issues[0].message, /WARNING/);
});

test("an unrecognised semantics value becomes null rather than a guess", () => {
  const envelope = parseEnvelope<unknown>({ data: {}, meta: { semantics: "as_of" } });
  assert.equal(envelope.meta.semantics, null);
});

test("decision time is read only when the route sent one", () => {
  assert.equal(parseEnvelope<unknown>(FULL).meta.decisionTime, null);
  const filtered = parseEnvelope<unknown>({
    data: [],
    meta: { decision_time: "2026-03-03T11:42:15Z" },
  });
  assert.equal(filtered.meta.decisionTime, "2026-03-03T11:42:15Z");
});

test("screens read the axes the response answered on, not the ones requested", () => {
  assert.deepEqual(answeredAxes(parseEnvelope<unknown>(FULL).meta), {
    marketTime: "2026-03-03T11:42:15Z",
    knowledgeTime: "2026-03-03T11:42:15Z",
  });
});

test("unknown meta keys survive untouched for the provenance panel", () => {
  const envelope = parseEnvelope<unknown>({
    data: {},
    meta: { mode: "PAPER", live_execution_available: false },
  });
  assert.equal(envelope.meta.raw.mode, "PAPER");
  assert.equal(envelope.meta.raw.live_execution_available, false);
});
