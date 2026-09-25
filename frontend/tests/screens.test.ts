/**
 * The screen registry. Phase 12 brief §5 and `18-ROADMAP.md` Phase 12 acceptance.
 *
 * The acceptance criterion these tests encode is "No screen ships ahead of its
 * backend — no 'Coming soon' pages", checked against the contract generated from
 * `oipulse/api/*.py` rather than against a list someone maintained by hand.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { ROUTE_PATHS } from "@/lib/api/contract.generated";
import {
  COMMAND_CENTER,
  SCREENS,
  WITHHELD_SCREENS,
  WORKFLOW_SCREENS,
  allDeclaredApis,
  screenById,
} from "@/lib/terminal/screens";

test("all fourteen named screens ship, none ahead of its backend", () => {
  // `13-FRONTEND_IA.md` §2 names fourteen: the Command Center and thirteen
  // workflow screens. The first Phase 12 pass shipped twelve of the thirteen,
  // withholding Journal for having no read API; the remediation added the
  // `/journal` contract, so the no-stub condition now holds for all of them.
  assert.equal(WORKFLOW_SCREENS.length, 13);
  assert.equal(SCREENS.length, 14);
  assert.equal(SCREENS[0].id, COMMAND_CENTER.id);
});

test("every API a screen declares exists in the backend", () => {
  for (const screen of SCREENS) {
    assert.ok(screen.apis.length > 0, `${screen.id} declares no backend`);
    for (const key of screen.apis) {
      assert.ok(ROUTE_PATHS.has(key), `${screen.id} declares ${key}, which does not exist`);
    }
  }
});

test("nothing is withheld, and the list survives for when something is", () => {
  assert.equal(WITHHELD_SCREENS.length, 0);
});

test("Journal ships against a real backend, not a placeholder", () => {
  const journal = screenById("journal");
  assert.notEqual(journal, undefined);
  assert.equal(journal?.route, "/terminal/journal");
  // The screen's claim is checked against the generated contract, so it cannot
  // declare a capability the backend does not serve.
  for (const key of journal?.apis ?? []) {
    assert.ok(ROUTE_PATHS.has(key), `${key} does not exist`);
  }
  assert.ok(ROUTE_PATHS.has("GET /journal/entries"));
});

test("each screen answers exactly one question, and states it", () => {
  for (const screen of SCREENS) {
    assert.match(screen.question, /\?$/, `${screen.id} has no question`);
    assert.equal(screen.question.split("?").length - 1, 1, `${screen.id} asks two questions`);
  }
});

test("routes are unique and live under /terminal", () => {
  const routes = SCREENS.map((s) => s.route);
  assert.equal(new Set(routes).size, routes.length);
  for (const route of routes) assert.match(route, /^\/terminal(\/|$)/);
});

test("ids are unique", () => {
  const ids = SCREENS.map((s) => s.id);
  assert.equal(new Set(ids).size, ids.length);
});

test("every screen is temporal, so both axes apply everywhere", () => {
  // `13` §1.6: "Every screen works at `now` or at a historical `(market_time,
  // knowledge_time)` pair, using the same components."
  for (const screen of SCREENS) {
    assert.equal(screen.temporal, true, `${screen.id} is not time-parameterised`);
  }
});

test("every screen names its interactions and its tests", () => {
  for (const screen of SCREENS) {
    assert.ok(screen.interactions.length > 0, `${screen.id} lists no interactions`);
    assert.ok(screen.tests.length > 0, `${screen.id} lists no tests`);
    assert.ok(screen.domains.length > 0, `${screen.id} names no backend domain`);
  }
});

test("the declared API set is deduplicated and sorted", () => {
  const declared = allDeclaredApis();
  assert.deepEqual([...declared], [...new Set(declared)].sort());
});

test("no screen declares a live-trading route", () => {
  for (const key of allDeclaredApis()) {
    assert.equal(
      key.includes("/trading/") && !key.includes("/paper-trading/"),
      false,
      `${key} is not a paper route`,
    );
  }
});

test("the eleven backend domains that exist are all reachable from some screen", () => {
  const covered = new Set(SCREENS.flatMap((s) => s.domains));
  for (const domain of [
    "MARKET_STATE",
    "ANALYTICS",
    "SIGNALS",
    "ALERTS",
    "RESEARCH",
    "REPLAY",
    "BACKTEST",
    "PAPER_TRADING",
    "RISK",
    "OMS",
    "PORTFOLIO",
  ]) {
    assert.ok(covered.has(domain as never), `${domain} is not reachable from any screen`);
  }
});
