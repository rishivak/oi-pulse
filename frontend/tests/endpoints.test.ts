/**
 * Endpoint construction, and its correspondence to the generated backend contract.
 *
 * Phase 12 brief §24. The cross-check against `ROUTE_PATHS` is the runtime twin of
 * `tools/check_frontend_contract.py`: the guard proves it statically for every call
 * site, this proves it for the values those call sites actually produce.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { ROUTE_PATHS } from "@/lib/api/contract.generated";
import {
  alerts,
  backtest,
  endpoint,
  endpointUrl,
  features,
  market,
  ops,
  paperTrading,
  portfolio,
  queryString,
  reconciliation,
  replay,
  research,
  risk,
  signals,
} from "@/lib/terminal/endpoints";

const GROUPS = {
  ops,
  market,
  features,
  signals,
  alerts,
  research,
  replay,
  backtest,
  paperTrading,
  risk,
  reconciliation,
  portfolio,
};

/** Arguments good enough to build every endpoint; values are irrelevant to the check. */
const ARGS: readonly unknown[] = ["x", 1, { underlying_id: 1, account_id: "a" }];

test("every endpoint builder produces a template the backend actually serves", () => {
  let checked = 0;
  for (const [groupName, group] of Object.entries(GROUPS)) {
    for (const [name, build] of Object.entries(group)) {
      const fn = build as (...args: unknown[]) => { method: string; template: string };
      const ep = fn(...ARGS.slice(0, fn.length));
      const key = `${ep.method} ${ep.template}`;
      assert.ok(
        ROUTE_PATHS.has(key),
        `${groupName}.${name} builds ${key}, which is not in the generated contract`,
      );
      checked += 1;
    }
  }
  // A guard that silently checked nothing would pass too.
  assert.ok(checked >= 60, `only ${checked} endpoints were exercised`);
});

test("an unsubstituted path parameter is an error, not a 404 later", () => {
  assert.throws(
    () => endpoint("GET", "/signals/{signal_id}"),
    /path parameter \{signal_id\} was not supplied/,
  );
});

test("path parameters are encoded", () => {
  assert.equal(endpoint("GET", "/signals/{signal_id}", { signal_id: "a/b" }).path, "/signals/a%2Fb");
});

test("absent query values are omitted rather than sent as the string null", () => {
  assert.equal(
    queryString({ a: 1, b: null, c: undefined, d: "", e: false }),
    "?a=1&e=false",
  );
});

test("both time axes travel as named parameters", () => {
  const url = endpointUrl(
    market.state({
      underlying_id: 1,
      market_time: "2026-03-03T11:42:00Z",
      knowledge_time: "2026-03-05T09:00:00Z",
    }),
  );
  assert.match(url, /market_time=2026-03-03T11%3A42%3A00Z/);
  assert.match(url, /knowledge_time=2026-03-05T09%3A00%3A00Z/);
});

test("there is no live-trading endpoint to call", () => {
  const names = Object.values(GROUPS).flatMap((g) => Object.keys(g));
  for (const name of names) {
    assert.doesNotMatch(name, /^live/i, `endpoint builder ${name} looks like a live path`);
  }
  const templates = [...ROUTE_PATHS];
  assert.equal(
    templates.some((t) => t.includes("/trading/") && !t.includes("/paper-trading/")),
    false,
    "the generated contract contains a non-paper trading route",
  );
});

test("there is no risk-approval endpoint; approval is only ever a server output", () => {
  assert.equal(Object.keys(risk).includes("approve"), false);
  assert.equal([...ROUTE_PATHS].some((t) => t.includes("/risk/approve")), false);
});
