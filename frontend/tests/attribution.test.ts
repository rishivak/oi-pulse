/**
 * Attribution display. Phase 12 brief §16, Phase 11 brief §13.
 *
 * The residual is the thing a presentation layer is most tempted to remove, because
 * a chart without it looks finished. These tests make its presence a property of
 * the API rather than a habit of whoever wrote the chart.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  ATTRIBUTION_HIERARCHY,
  RESIDUAL_ROW_ID,
  UNATTRIBUTED,
  type AttributionInput,
  attributionRows,
  chartIncludesResidual,
  chartSegments,
  reconciliationNotice,
  residualShare,
} from "@/lib/terminal/attribution";

const INPUT: AttributionInput = {
  bucket: "PORTFOLIO",
  bucketId: null,
  totalPnl: "1250.00",
  explained: "1100.00",
  residual: "150.00",
  residualFraction: 0.12,
  reconciles: true,
  method: "GREEK_EXPLAIN_SECOND_ORDER",
  methodVersion: 1,
  components: [
    { component: "DELTA", amount: "900.00" },
    { component: "GAMMA", amount: "150.00" },
    { component: "VEGA", amount: "60.00" },
    { component: "THETA", amount: "-10.00" },
  ],
  uncomputedComponents: [],
};

test("the residual is always a row, including when it is zero", () => {
  const zero = { ...INPUT, residual: "0.00", residualFraction: 0 };
  for (const input of [INPUT, zero]) {
    const rows = attributionRows(input);
    const residual = rows.filter((r) => r.isResidual);
    assert.equal(residual.length, 1);
    assert.equal(residual[0].id, RESIDUAL_ROW_ID);
  }
});

test("the residual is always a chart segment; a balanced chart is not achievable by omission", () => {
  assert.equal(chartIncludesResidual(chartSegments(INPUT)), true);
  assert.equal(
    chartIncludesResidual(chartSegments({ ...INPUT, residual: "0" })),
    true,
  );
});

test("no component absorbs the residual: components are unchanged when it changes", () => {
  const big = { ...INPUT, residual: "99999.00" };
  const componentsOf = (i: AttributionInput) =>
    attributionRows(i).filter((r) => !r.isResidual).map((r) => `${r.id}=${r.amount}`);
  assert.deepEqual(componentsOf(INPUT), componentsOf(big));
});

test("the residual is read from the backend, never derived by subtraction here", () => {
  // Supply figures that do not sum, and assert the displayed residual is still the
  // backend's number. A frontend that recomputed would show -100 and would have
  // quietly overruled the authority the database constraint enforces.
  const inconsistent = { ...INPUT, explained: "1350.00", residual: "150.00" };
  const residual = attributionRows(inconsistent).find((r) => r.isResidual);
  assert.equal(residual?.amount, "150.00");
});

test("the reconciliation flag is reported, not recomputed", () => {
  const notice = reconciliationNotice({ ...INPUT, reconciles: false });
  assert.equal(notice.reconciles, false);
  assert.match(notice.detail, /nothing is adjusted to make them agree/);
  assert.notEqual(notice.glyph, "");
});

test("the residual row explains itself rather than appearing as a stray number", () => {
  const residual = attributionRows(INPUT).find((r) => r.isResidual);
  assert.match(residual?.note ?? "", /Not allocated to any component/);
});

test("an uncomputed component is marked, not dropped and not shown as zero", () => {
  const rows = attributionRows({
    ...INPUT,
    components: [...INPUT.components, { component: "RHO", amount: "0" }],
    uncomputedComponents: ["RHO"],
  });
  const rho = rows.find((r) => r.id === "RHO");
  assert.equal(rho?.uncomputed, true);
});

test("the unattributed bucket is labelled as such", () => {
  const rows = attributionRows({
    ...INPUT,
    components: [{ component: UNATTRIBUTED, amount: "12.00" }],
  });
  assert.equal(rows[0].label, "Unattributed");
  assert.match(rows[0].note ?? "", /attributes to no named component/);
});

test("the hierarchy is the Phase 11 one, in order", () => {
  assert.deepEqual(ATTRIBUTION_HIERARCHY, [
    "PORTFOLIO",
    "STRATEGY",
    "UNDERLYING",
    "INSTRUMENT",
    "TRADE",
  ]);
});

test("the residual share is shown when the backend supplied a fraction", () => {
  assert.equal(residualShare(INPUT), "12.0% of total P&L");
  assert.equal(residualShare({ ...INPUT, residualFraction: null }), null);
});

test("slicing or filtering the components cannot lose the residual", () => {
  const rows = attributionRows(INPUT);
  const components = rows.filter((r) => !r.isResidual);
  // The residual is not one of the components, so no component-level operation
  // can remove it; it is reattached by `attributionRows` on every call.
  assert.equal(components.some((r) => r.isResidual), false);
  assert.equal(attributionRows(INPUT).at(-1)?.isResidual, true);
});
