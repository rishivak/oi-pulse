# Phase 12 — Professional Terminal: implementation report

> **Remediation pass.** Independent verification returned `PHASE 12: NOT VERIFIED`
> with security blockers (no authentication, no authorization, no CSRF) and two
> specification decisions to settle (Journal, charting). Sections 1–16 below are the
> original report, corrected where the remediation changed the facts; **§17 onward is
> the remediation record** and is the part written for the re-verification.

**Branch** `phase12-professional-terminal`
**Base** `c2482acd8818d79bda04a883dffa959fc58808d7` (`origin/main`)
**Previous verified checkpoint** `4923cdda8bd47e9286a3f0692e93fb5b8a4d74aa` — present in
`origin/main` with an **identical tree** (`git diff --exit-code` returns nothing). The tag
`oi-pulse-v2-phase11` does not exist in this clone; only `oi-pulse-v2-phase3` and
`oi-pulse-v2-phase4` do, and the same has been true since Phase 7.

Phase 12 is the final phase. No Phase 13 exists and none is created.

---

## 1. What was built

A v2 operator and research terminal at `/terminal/*`, consuming the verified Phase 3–11
HTTP contracts and holding no domain logic of its own.

```
frontend/app/terminal/          13 screens + layout, error, loading, not-found
frontend/components/terminal/   12 presentation components
frontend/lib/terminal/          22 pure TypeScript modules (the decisions)
frontend/lib/api/               generated contract + typed DTOs
frontend/tests/                 18 test files, 204 tests, runnable with no packages
tools/export_api_contract.py    generates the contract from oipulse/api/*.py
tools/check_frontend_contract.py   guard: no screen ahead of its backend
tools/check_terminal_boundary.py   guard: the terminal stays a presentation layer
tools/check_terminal_imports.py    guard: every import and export resolves
tests/phase12/                  65 tests, of which 32 are guard mutation tests
```

The architecture is deliberate and is what makes the phase verifiable in this
environment: **every decision lives in a pure module, and the components render what
those modules decided.** Whether a value is absent, what a status means, whether an order
may be cancelled, whether a retry is permitted, whether the residual appears — each is a
function with tests, not a judgement inside JSX. It also means the safety-critical
behaviour is covered by tests that run here, where no UI toolchain can be installed.

### The legacy app is untouched

`frontend/app/(dashboard)` and `frontend/lib/api/client.ts` are the **v1** UI, talking to
the separate `backend/` application over `/oi/*` and `/auth/*`. `18-ROADMAP.md`'s cutover
and legacy removal is its own operational step, not part of Phase 12, so the v2 terminal
was added beside it. `next.config.mjs` now proxies both: `/api/*` to the legacy backend as
before, `/api/v2/*` to `oipulse/`.

---

## 2. Screen inventory

`13-FRONTEND_IA.md` §2 draws **fourteen** names. `18-ROADMAP.md` Phase 12's objective says
"The twelve workflow screens" while its deliverables list all fourteen.
`20-ARCHITECTURE_FREEZE.md` §286 states the gate: **"twelve screens, none ahead of its
backend"**.

The qualifier resolves it. **Journal has no read API** — `journal_entries` is created by
migration 0008 and written by the Phase 8 runtime, `12-API_SPEC.md` §3 specifies
`/journal`, and no router serves it. `13` §2 forbids shipping it anyway: *"Screens appear
only when their backend capability is real. No 'Coming soon' pages — the legacy app
shipped three stubs over working endpoints, which is worse than not listing them."* Remove
Journal and treat the Command Center as the router it is described as being, and all three
statements hold: twelve workflow screens, plus the Command Center, none ahead of its
backend.

Journal is recorded in `WITHHELD_SCREENS` with its reason and its unblocking condition, and
is named on the terminal's `not-found` page. It is not rendered, not linked, and not
present as a disabled item.

| # | Screen | Question | Route | Backend | Key interactions |
|---|---|---|---|---|---|
| — | Command Center | What is happening, and where should I look? | `/terminal` | MarketState, Analytics, Signals | routes to every workflow screen carrying the shared state; contradictions panel |
| 1 | Option Surface | What does the chain look like? | `/terminal/option-surface` | `GET /market/state` | functional expiry selector; per-field staleness |
| 2 | Positioning | Where is positioning, and where is it moving? | `/terminal/positioning` | `GET /features`, `/features/{id}/values`, `/features/{id}/versions/{v}` | registry definitions beside values; missing features named |
| 3 | Volatility | What is volatility doing? | `/terminal/volatility` | same three | IV rank renders insufficient-history, never a zero |
| 4 | Market Structure | What levels and what regime? | `/terminal/market-structure` | features + `GET /market/state` | GEX with its dealer convention |
| 5 | Signals | What is developing, and why? | `/terminal/signals` | `/signals`, `/signals/{id}`, `/{id}/history`, `/types`, `/types/{t}/versions/{v}` | supporting vs contradicting evidence; invalidation condition |
| 6 | Alerts | What do I want to be told about? | `/terminal/alerts` | seven `/alerts/*` routes | dry run; acknowledgement that states what it did not change |
| 7 | Research | Does this relationship exist? | `/terminal/research` | nine `/research/*` routes | raw events beside effective sample; exclusion counts |
| 8 | Replay | What did it look like as it happened? | `/terminal/replay` | five `/replay/*` routes | persistent two-axis banner; transport with no order verb |
| 9 | Backtest | Would this have worked? | `/terminal/backtest` | seven `/backtest/*` routes | assumptions beside the headline |
| 10 | Paper Trading | What would this trade do? | `/terminal/paper-trading` | twelve `/paper-trading/*` + `POST /risk/evaluate` | intent builder, server-side risk preview, blotter with `UNKNOWN`, provenance trail |
| 11 | Portfolio | What do I hold, and how is it performing? | `/terminal/portfolio` | ten `/portfolio/*` routes | positions incl. closed, attribution with the residual, completeness notice |
| 12 | Risk | What are my limits and utilization? | `/terminal/risk` | eight `/risk/*` + eight `/reconciliation/*` | limits by the four statuses, decision history, kill switch, OMS vs provider columns |

Every route key above is checked against the generated contract by
`tools/check_frontend_contract.py`, and the registry↔page correspondence by
`tools/check_terminal_boundary.py`. A screen cannot declare a backend it does not have, and
a page cannot exist without a registry entry.

---

## 3. Contracts: generated, not transcribed

`fastapi` cannot be installed here, so no OpenAPI document can be produced.
`tools/export_api_contract.py` reads the same information out of the source with `ast` and
emits `frontend/lib/api/contract.generated.{json,ts}`:

- **82 routes** — method, path, operation, module and the literal `meta` keys.
- **77 models** — every `as_dict` in `oipulse/`, as class → key set.
- **35 serializers** — every `*_to_dict` in a `serialisation.py`.

Anything the parser cannot resolve is marked `partial`, and a partial entry is used only as
a lower bound, so it can never produce a false accusation.

Field *names* are therefore generated. Field *types* are hand-written in
`frontend/lib/api/dto.ts`, because the backend is untyped on the wire (`dict[str, Any]`
returns) and the exporter reads names, not types. Every interface carries a `@contract`
tag naming the generated entry it describes, and the guard rejects any field that entry
does not send. **This found a real defect during implementation**: `OrderDto` declared
`market_time`, which `PaperOrder.as_dict()` does not carry.

---

## 4. Temporal UX

Both axes, always, and never one unlabelled "time".

- `lib/terminal/time.ts` returns **labelled pairs**. There is no function that produces a
  single timestamp for display.
- `resolveSemantics` maps the pair onto the repository mode; `knowledge_time < market_time`
  throws `IncoherentTimeAxes` before a request is built, rather than collecting a 422.
- `isHindsight` is **computed**, never a stored toggle, so the banner and the request
  cannot disagree. The indicator is `persistent: true` for as long as the state holds.
- `decision_time` is modelled as a derived request value, not a member of `TimeAxes` —
  `05` §2 and `12` §2 make it a request parameter, never a stored field.
- The axes live in the URL, so any view is linkable and the expiry selector cannot go inert:
  the request is built from the URL, which is the `expiries[0]` defect (`13` §4) designed
  out rather than fixed.
- Replay renders `replayBanner`, whose `persistent` field is the literal `true`.
- All instants render in IST with the zone labelled and UTC on hover, using a fixed
  UTC+05:30 offset rather than an `Intl` zone lookup — server and browser must not disagree.
- Where `observed_at` and `ingested_at` differ by ≥ 1 s, both are shown.

---

## 5. Quality, availability and the three forbidden substitutions

Brief §18 names them: `missing → 0`, `unavailable → previous`, `unreliable → latest`.

The defence is structural rather than careful. `presentValue` returns a discriminated
union, so a component receives either a value or an `Absent` carrying its reason — there is
no code path on which a bare number might secretly be a stand-in. `presentValue` takes no
prior-value argument, so `unavailable → previous` is not a rejected policy but an
inexpressible one. The `Value` component has no `fallback` prop.

- An **absent or unrecognised** quality status becomes `UNKNOWN`, never `OK`. A route that
  stopped sending quality must not start looking healthy.
- `UNKNOWN` and `UNRELIABLE` both **withhold** derived values, not merely desaturate them —
  a desaturated but readable number still gets read.
- Zero is a value and survives as one.
- Five distinct empty states, each with its own sentence; `NO_DATA_FOR_PERIOD` says
  explicitly *"This is not a zero."*
- `QueryPanel` orders the non-success states so loading wins over error and error wins over
  empty. A failed request is never shown as "no data for this period".
- A 503 renders the backend's own reason. `oipulse/api/signals.py` answers 503 rather than
  `[]` because *"an empty list is indistinguishable from no signals having fired"*, and the
  terminal preserves that distinction.

---

## 6. Provenance and evidence

Two traversals, both built under one rule: **a link is either a real reference or an
explicit gap.**

- The decision chain (brief §19): Signal → Evidence → Strategy → TradeIntent → RiskDecision
  → OMS Order → Fill → Position → P&L.
- The metric drill-through (`13` §7): value → feature definition → inputs → MarketState →
  raw observations.

`chainStep` recognises the backend's `not recorded` sentinel and renders it as a gap in its
canonical position, with the reason — never as a link to nowhere and never as prose
standing in for a reference (§19: *"Do not replace evidence with free-text explanations"*).
`summariseChain` reports a partial chain as partial; `inCanonicalOrder` is asserted in
tests, because a trail rendered out of order stops being a traversal.

`contradiction_assessment` stays a union on the wire and in the DTO. "Assessed, found
nothing" and "has contradicting evidence" are different claims, and flattening the first to
an empty array would lose it.

---

## 7. Attribution and the residual

- `attributionRows` appends the residual **unconditionally**, including when it is zero.
  There is no flag to suppress it.
- The residual is appended *after* the components rather than merged into them, so slicing,
  sorting or filtering the components cannot drop it by accident.
- `chartSegments` includes it as a segment, so a balanced-looking bar is not achievable by
  omission. Segment widths are proportional to the amounts *as supplied*.
- **Nothing is recomputed.** `residual` is read from Phase 11, where it is a derived
  property and a `CHECK` constraint enforces `total_pnl = explained + residual`. A frontend
  subtraction in JavaScript floating point against decimal strings could only disagree with
  that authority, and would disagree in the direction of declaring a correct result broken.
  `reconciliationNotice` reports the backend's own `reconciles` flag for the same reason.
- `tools/check_terminal_boundary.py` fails on any subtraction involving an attribution
  field, anywhere in the terminal.
- A test decomposes identical components against very different totals and asserts no
  component value moves.

---

## 8. Trading safety

Live trading cannot render, and the reason is structural at four levels:

1. `liveTradingRenderable()` is typed to return the **literal `false`**. No caller can be
   written that handles a `true` branch, and the guard fails if the annotation is widened.
2. `lib/terminal/endpoints.ts` contains no live route, and the generated contract contains
   no `/trading/*` path that is not `/paper-trading/*`.
3. `executionMode` returns `UNKNOWN` unless a response states `mode: PAPER` **and**
   `live_execution_available: false`. Order entry is withheld on `UNKNOWN` — an unlabelled
   account is never assumed to be simulated.
4. The three gates of `17-SECURITY.md` §4 are recorded with the evidence for each being
   closed, and none can hold in this build.

`UNKNOWN` order state is treated as `11-TRADING.md` §5 requires: never assumed rejected,
never assumed accepted, never resubmitted, and it blocks its `(strategy, instrument)` pair —
keyed on the pair, so another strategy's unknown order does not halt unrelated work. A
state this build does not recognise blocks too. A live order with no recorded provider id
cannot be cancelled, because the id needed to send the cancel does not exist.

**Approvals are never manufactured in the UI.** The preview button posts to
`/risk/evaluate`, which runs the engine; `riskPreview` reads the server's `is_approved`,
never inferring approval from an absence of breaches; and an approval that is no longer
`actionable` does not permit submission.

**No write is retried automatically.** `retryDecision` checks the method first and returns
`false` for every non-GET regardless of the error code; `useTerminalMutation` sets
`retry: false`; the transport itself never reissues. A repeated intent submission is
reported as `duplicate` — the operator learns nothing was re-applied, rather than seeing a
second order.

---

## 9. Real-time: the honest gap

`12-API_SPEC.md` §3 specifies `GET /stream/events` with six channels. **It is not
implemented** — `oipulse/api/app.py` mounts eleven routers and none serves `/stream`.

Brief §21 forbids inventing a stream around it, so there is no `EventSource` anywhere in
the terminal and the boundary guard fails on one. Live views poll at 30 s and the header
says `POLLED — NOT A LIVE FEED`, with the explanation that an unchanged value may mean
nothing moved *or* that nothing has been re-read. Historical views do not refresh at all: a
past `(market_time, knowledge_time, build_context_id)` is immutable (`12` §5), so
re-requesting it can only return the same bytes.

A test asserts `STREAM_ENDPOINT_IMPLEMENTED` agrees with the generated contract, so the
constant cannot drift away from the backend in either direction.

---

## 10. Security

- **No `process.env` anywhere in the terminal tree**, checked by the guard. The v2 backend
  address is `OIPULSE_V2_API_URL`, read only in `next.config.mjs`; it deliberately has no
  `NEXT_PUBLIC_` prefix, so Next does not inline it into the client bundle.
- The API base is a fixed same-origin prefix. No parameter, header or setting can redirect
  a request elsewhere (§23, "no arbitrary backend URL access").
- One request surface. `fetch`, `XMLHttpRequest` and `axios` outside
  `lib/terminal/client.ts` are guard failures — otherwise the contract check would cover
  only part of the traffic.
- No database, cache, ORM, provider or broker client may be imported.
- The session cookie travels `same-origin`; the double-submit CSRF token is sent on every
  state-changing method.
- Alert rule `destination` is echoed as configured and is never a credential —
  `oipulse/alerts/serialisation.py` resolves destinations from deployment configuration.

**Stated plainly: the backend implements no authentication and no CSRF validation.**
`17-SECURITY.md` §3 and §7 specify server-side sessions and the three-part CSRF defence;
`oipulse/api/app.py` mounts no middleware for either. The terminal sends the token —
the half of the contract it owns — and the client module says in its own docstring that the
server does not yet check it. This is a gap in the system, not in Phase 12, and it is
listed in §13.

---

## 11. Accessibility

- Status is never colour alone. Every badge — quality, order state, signal status,
  discrepancy, mode, realtime, sync — carries a glyph and a screen-reader label alongside
  its tone.
- Tables go through `DenseTable`, which supplies `<caption>` and `scope="col"`; the guard
  fails on a raw `<table>` outside the primitives module.
- Every interactive element carries a visible focus ring
  (`focus-visible:outline-2 outline-accent`); navigation, transport, expiry selection,
  cancel and the kill switch are all real `<button>`/`<select>`/`<label>` elements reachable
  by keyboard.
- Loading regions are `role="status" aria-live="polite" aria-busy`; errors are `role="alert"`.
- Sections are labelled with `aria-label`; the active nav item carries `aria-current="page"`.
- Units are in the column header once, not repeated per cell.

**Not run:** `axe`, `eslint-plugin-jsx-a11y` or any automated audit. No accessibility tool
can be installed here. What is above is what the code does by construction; contrast ratios
and screen-reader behaviour have not been measured.

---

## 12. Verification actually performed

Baseline measured at `origin/main` **before any edit**: 1392 tests, 0 failures, 55 errors,
44 skipped; 13/13 guards PASS.

| Check | Command | Result |
|---|---|---|
| Python suite | `python3 -m unittest discover -s tests -t .` | **Ran 1457** · 0 failures · 55 errors · 44 skipped |
| Terminal suite | `npm test` (`node --test`, no packages) | **204 tests, 204 pass, 0 fail** |
| Guards | each `tools/check_*.py` | **16/16 PASS** |
| Contract currency | `python3 tools/export_api_contract.py --check` | PASS — 82 routes, 77 models, 35 serializers |
| Lint | `ruff check .` | All checks passed |
| Format | `ruff format --check .` | 333 files already formatted |
| Bytecode | `python -m compileall -q oipulse tools tests` | clean |

Errors are 45 × `No module named 'fastapi'` and 10 × `No module named 'google'` —
**identical in count and kind to the baseline**, with no error of any other type. The
1392 → 1457 delta is exactly the 65 tests in `tests/phase12`; no pre-existing test was
added to, removed, weakened or skipped.

### Mutation-tested guards

Brief §30 asks for guards; the Phase 10 brief §29 warns that "safety guard PASS" is not
"mutation-tested safety". **32 of the 65 Phase 12 tests break exactly one thing in a
disposable copy of the tree and assert the guard names it.** Two more add a *valid*
construct and assert the guard stays silent, pinning false positives that were real; four
establish that the guards pass on an untouched copy — so a later failure is attributable to the mutation
rather than to the copying. The mutation helper raises when its anchor string is missing,
so a test cannot silently stop mutating and pass for the wrong reason, which is a failure
mode this project has hit before.

Covered mutations: an endpoint the backend does not serve · a screen declaring a
nonexistent route · a DTO field the backend does not send · an untagged DTO · a DTO citing
a nonexistent model · a component building its own request · a stale contract · a parser
that has stopped matching · a database import · a provider import · `process.env` ·
a credential identifier · an `EventSource` · a widened live-trading return type · arithmetic
in a component · a frontend-derived residual · a non-erasable TypeScript construct · a
later-phase reference · a page bypassing `ScreenFrame` · a registry entry with no page · a
page not in the registry · a raw `<table>` · a missing `"use client"` · a guard pointed at
an empty tree.

### Two defects the checks caught during implementation

1. **The DTO field check was matching nothing.** The field regex lacked `re.MULTILINE`, so
   `^` anchored to the start of the whole interface body and zero fields were compared —
   every DTO passed while verifying nothing. A mutation test found it. The guard now counts
   the fields it compared and fails if the count collapses, and the fix immediately
   surfaced a real error (`OrderDto.market_time`, which the backend does not send).
2. **The route group would have broken the build.** The terminal was first written under
   `app/(terminal)/`. Next.js strips `(group)` segments from the URL, so
   `app/(terminal)/page.tsx` resolves to `/` and would have collided with the legacy
   dashboard's root page. It is now a real `app/terminal/` segment.

A third, smaller one is worth recording because it is a **recurring mistake in this
project**: the "no coming soon" test initially failed on `not-found.tsx`, whose comment
explains that the design *forbids* a "coming soon" page. A ban on a word catches its own
disclaimer. The test now strips comments first — the same correction applied in Phases 7,
8 and 11.

### Environment-blocked — not run, and not claimed

Reported separately per the Phase 10 brief §29: type-checking source is not type-checking,
and a guard PASS is not a build.

| Blocked | Why | Consequence |
|---|---|---|
| `npm ci` / any install | `registry.npmjs.org` is denied by the sandbox; `--offline` fails `ENOTCACHED` | nothing below can run locally |
| `npm run build` | needs `next` | **the production build has never run.** Route tree, server/client split and module resolution are unverified by a compiler |
| `npm run type-check` (`tsc --noEmit`) | needs `typescript` | **no TypeScript type has been checked.** Every `.ts`/`.tsx` type in this phase is unverified |
| `npm run lint` (`next lint`) | needs `eslint` | unverified; a scripted scan for unused imports was run instead and is clean |
| React component render tests | needs a DOM and a test runner | **no component has been rendered.** Coverage is at the view-model layer |
| Accessibility audit | needs `axe`/`jsx-a11y` | contrast and screen-reader behaviour unmeasured |
| `mypy --strict` | no package index | `tools/check_typing_strict.py` is a stdlib subset with no inference |
| `pytest`, `lint-imports`, `alembic`, PostgreSQL, Redis | no package index / no server | as in Phases 7–11 |

All of these run in CI: a `frontend` job was added running `npm ci`, `npm test`,
`npm run type-check`, `npm run lint` and `npm run build`, and the three Phase 12 guards were
added to the `gate` job.

**The 204 terminal tests do run here**, and they are not a consolation prize — they cover
the temporal model, the three forbidden substitutions, the residual, the `UNKNOWN` order
rules, the no-live-trading path, error classification, retry policy and the contract
correspondence. Node 24 strips TypeScript types natively, so `node --test` runs the pure
modules with zero packages installed. That is exactly why the decisions were put in pure
modules.

---

### `tools/check_terminal_imports.py` — standing in for the part of `tsc` that matters most

Added after the main implementation, in direct response to risk 1 below. `tsc --noEmit`
and `next build` cannot run here, and the errors they most often catch — a mistyped path,
a renamed export, a symbol that was never exported, a page missing its default export —
are resolvable from source alone. This guard resolves them:

- **233 local imports** resolved to real files, trying the extensions and `index` files a
  bundler would try.
- **487 named imports** compared against the actual export list of the module they name,
  following `export * from` one level.
- every default import checked against a default export, and every `page.tsx` checked for
  one, because Next's error for a missing page default does not say which file.
- external packages checked against `package.json`, since their files cannot be resolved
  without `node_modules`.
- brackets balanced, as a smoke check for a truncated file.

All of it passes. Twelve mutation tests cover it, including two that assert the *absence*
of a false positive: building the bracket check required telling a regex literal from a
division, and the first two attempts misread JSX closing tags (`</div>`) and self-closing
tags (`... />`) as regexes, reporting nine intact files as truncated. Both heuristics are
now pinned by tests.

**This is not a type check.** It resolves names, not types: a `string` passed where a
`number` is required passes here and fails in `tsc`. It makes a build failure less likely;
it does not make one impossible.

---

## 13. Known limitations

1. **No type check, no build, no component render.** The three checks that would most
   directly validate a UI phase have not been run. They are wired into CI; until CI runs,
   the TSX is unverified by any compiler.
2. **Backend authentication and CSRF do not exist.** `17-SECURITY.md` §3, §4 and §7 specify
   server-side sessions, five permissions and three-part CSRF defence. None is implemented
   in `oipulse/api/`. The terminal sends the CSRF token and relies on same-origin cookies;
   there is nothing to validate either. Permission-driven UI (§23's authorization boundary)
   therefore cannot be implemented against anything real, and is not faked.
3. **`/features/{id}/values` needs a `metric_reader` that Phase 4 did not wire.** The route
   exists and answers 503 with an explicit reason when the reader is absent; the terminal
   renders that as "store unavailable" rather than an empty chart. The same is true of
   `/market/state` (`state_service`) and `/signals` (`signal_reader`). The screens are not
   ahead of their backends — the routes are real — but a deployment without readers shows
   the degraded panel on the four analysis screens and the Command Center.
4. **The feature value row shape is reader-defined and unchecked.** `data` is whatever the
   configured reader produced; the DTO is permissive and every field is read defensively.
5. **The intent request body cannot be contract-checked.** `POST .../intents` takes an
   untyped `dict`. Field names were taken from `TradeIntent.as_dict()` in the generated
   contract, which is the closest available evidence, but the guard cannot verify them.
6. **Charts are tables.** `lightweight-charts` is a declared dependency and is not used:
   an unverifiable charting integration in a phase with no build was a worse trade than
   dense, correct tables. The equity curve, IV series, skew curve, term structure and GEX
   profile render as their underlying values. This is a real shortfall against `13` §4.
7. **Signal detail evidence is partial.** `GET /signals` returns signals without supporting
   evidence (`include_evidence=False`); the screen says so and points to the detail route
   rather than showing an empty "supporting" column as though none existed.
8. **Journal is not built** (§2), and **no SSE** (§9).
9. **One pre-existing formatting defect was fixed in a separate commit.**
   `tests/phase11/test_attribution_and_reconciliation.py` had a trailing blank line that
   `ruff format --check` rejects. It is unrelated to Phase 12 and was committed on its own
   so the phase commit stays clean.
10. **Guards are lexical, not type-aware.** They strip comments, strings and regex
    literals before matching, so they read code rather than prose, but a check here proves
    a token is absent or a name resolves — not that a behaviour is impossible and not that
    a type is right. `tsc` and `eslint` remain authoritative and run in CI.

---

## 14. Remaining risks

1. **The build may fail on first CI run.** Nothing here has been compiled. Mitigations
   applied, in descending order of what they rule out: `check_terminal_imports.py` resolves
   all 233 local imports and all 487 named imports, so no unresolved path or renamed export
   remains; the route-group bug was found and fixed; the segment is `force-dynamic` because
   every screen reads `useSearchParams`; a guard checks that every file using a hook or an
   event handler declares `"use client"`; every page is confirmed to have a default export.
   What remains unruled-out is a **TypeScript type error** — a wrong type on a field or a
   prop that resolves fine and does not check. That is the likeliest first failure, and
   nothing available here can find it.
2. **Payload field *types* are hand-written.** Names are checked; a `string` that is
   actually a `number` would pass every check in this phase and surface at runtime.
3. **The terminal is only as honest as the envelopes it receives.** Quality, mode and
   completeness are read from `meta`; a route that stopped sending them degrades safely
   (`UNKNOWN`, order entry withheld), but a route that sent them *wrongly* would be believed.
4. **Deploying the terminal without authentication** would expose the kill switch and paper
   order entry to anyone who can reach the origin. Limitation 2 is a gap in the system; this
   is its operational consequence and should gate any deployment.
5. **Density versus comprehension.** Thirteen screens of monospace tables is what the design
   asks for and what a professional terminal needs, but no user has looked at it. The
   one-question-per-screen constraint is the mitigation the design names; whether it worked
   is not something this phase can report.

---

## 15. Scope

Phase 12 only. No Phase 13 was created and a guard fails on any reference to one. No second
backend. No new external provider, queue or database. No domain semantics changed. No
verification tag created. Nothing merged into `main`.

---

## 16. Git

Two commits on `phase12-professional-terminal`, based on `c2482ac`:

| SHA | |
|---|---|
| `45713db` | `style: remove trailing blank line rejected by ruff format` — the unrelated pre-existing fix from §13.9, kept out of the phase commit |
| `fb4ba0f` | `Implement the professional terminal` — Phase 12 |

**Two facts worth recording rather than tidying away.**

`git checkout -b phase12-professional-terminal origin/main` created the branch ref but did
not move `HEAD`; the reflog shows no checkout entry for it. Both commits therefore landed
on the local `main` first. They were moved by pointing the branch at the Phase 12 commit,
checking it out, and resetting the local `main` back to `c2482ac`. Nothing was pushed at
any point, `origin/main` was never touched, and no history was rewritten — the correction
was to a local branch pointer only. The most likely cause is the same sandbox restriction
described next, aborting the worktree update after the ref was written.

`.env.example` shows as modified in `git status` in every phase since Phase 7 and cannot be
read by git in this environment (it is on the sandbox's deny-list, and `git diff --check`
reports `cannot hash .env.example`). It is not part of Phase 12, was excluded from both
commits, and remains untouched.

No verification tag was created. Nothing was merged into `main`.

---
---

# Phase 12 remediation

Independent verification returned `PHASE 12: NOT VERIFIED`. This section records what
was done about each finding.

**Starting point.** The verifier's final branch commit `982b663cd3d0a95a814fb511c261373879f28509`
**is not reachable in this environment**: `git fetch` fails on a read-only credential
lock, and `git ls-remote` shows `origin/phase12-professional-terminal` at `59307c2`,
the implementation lineage. The remediation therefore starts from the current branch
state — `59307c2` plus the import guard added after it — and anything the verifier
committed on top has not been seen. That is a gap in this pass and is stated rather
than assumed away.

---

## 17. Authentication — the blocker

`17-SECURITY.md` §3, implemented as the pipeline the brief §2 specifies:

```
Request → session extraction → identity_sessions lookup/validation → identity → route authorization
```

`oipulse/identity/gate.py`'s `evaluate()` is that pipeline, as **one pure function**
of a `RequestFacts` value. `oipulse/api/security.py` builds the facts from a Starlette
request and renders the decision; it is about a hundred lines of adapter.

That shape is the correction, not a preference. The first Phase 12 pass expressed its
security story in places this environment cannot execute, and an unexecuted security
control is a claim. Every rule below is covered by tests that run on a bare
interpreter, because `fastapi` cannot be installed here.

**Middleware, not per-route dependencies.** A dependency has to be remembered on each
route and a route added without it is open. The middleware runs on everything, and
`oipulse/identity/permissions.py` **closes by default**: a path with no policy entry
is refused, not served.

| Requirement | Where |
|---|---|
| server validated | `validate_session` against the stored record; no signed token exists anywhere |
| revocable | `revoked_at`; effective on the next request, not at end of TTL |
| expiry-aware | `expires_at`, inclusive at the instant |
| idle timeout | `last_seen_at` + 2 h, touched on each allowed request |
| tied to an identity | `user_id` → `Principal.identity_id` |
| anonymous refused | every non-public route, checked across the whole policy table |

**The three routes the brief names are tested explicitly**, anonymously, and refused
with 401: `POST /risk/kill-switch`, `POST /paper-trading/accounts/{id}/intents`,
`POST /reconciliation/trigger`. A fourth test walks **every** entry in the policy
table rather than a sample — a boundary that protects the routes someone remembered
is the boundary that was already missing.

**Refusals say little.** All four session failures render an identical 401 body, so a
probe cannot learn that a guessed id was real but revoked. The server still records
which, for the audit line. Session ids and CSRF tokens are structurally redacted in
`__repr__` (`17` §2).

### No login endpoint, deliberately

`17` §11: the legacy `/auth/offline-session` is "not carried forward **in any form**",
and no document in `docs/design/` specifies a replacement user login flow — §2's OAuth
is for broker credentials. So `issue_session()` is a server-side function with no HTTP
route, and **the gate is closed to everyone until an issuance path is specified**.

That is the direction this should fail in. A system nobody can sign into is
recoverable in an afternoon; an invented login endpoint is an authentication bypass
with good intentions, and inventing one is what §1 of the brief forbids.

---

## 18. Authorization — the five permissions

Exactly `17-SECURITY.md` §4's five, in its order, checked by a guard:
`MARKET_DATA_READ`, `RESEARCH`, `PAPER_TRADE`, `LIVE_TRADE`, `ADMIN`.

Enforcement is server-side at the API boundary, in middleware. **Hiding a UI control
is not authorization here and never was**: every authorization test invokes the
decision directly, with no UI in the path, and the HTTP suite posts to the routes.

The policy table maps all 84 routes. Highlights:

| Route group | Permission |
|---|---|
| `/market`, `/features`, `/signals` | `MARKET_DATA_READ` (§4: market state, analytics, signals) |
| `/research`, `/replay`, `/backtest` | `RESEARCH` (§4: studies, datasets, backtests, replay) |
| `/paper-trading`, `/portfolio`, `/journal`, most of `/risk` and `/reconciliation` | `PAPER_TRADE` |
| `POST`/`DELETE /risk/kill-switch`, `PUT /risk/profiles`, `POST /reconciliation/trigger`, `POST /portfolio/position-reconciliation` | `ADMIN` (§4 operational; §9 audits each) |
| `GET /ops/health`, `GET /ops/ready` | public |

**Cross-account access** is a second check, per §4's defence in depth: an
account-scoped route compares the account's owner against the principal, and refuses
with 403 when they differ. A process that cannot resolve ownership answers **503**
rather than serving without the check. `ADMIN` is the documented exception.

**Privilege escalation is tested at the parser.** A permissions snapshot containing
`SUPERUSER`, `admin`, `"ADMIN "`, `*` or `ADMIN;DROP` yields the empty set — an
unknown string is dropped rather than carried, so it cannot travel through logs
looking like a capability.

### Two places the specification does not reach

Both are inferences, marked as such in the code and here rather than presented as the
document's words.

1. **Alerts.** §4's table does not name them. They are delivery over signals, and
   Phase 5 keeps alert state strictly separate from signal truth, so an alert rule is
   a reader's own configuration rather than a system-administration object. Mapped to
   `MARKET_DATA_READ`.
2. **`/ops/health` and `/ops/ready`.** §4 puts "operational endpoints" under `ADMIN`,
   but `14-DEPLOYMENT.md` §172 wires these to container probes, which carry no
   session. They are the only two public routes, listed individually, and the guard
   fails if a third appears or if any becomes non-`GET`.

---

## 19. `LIVE_TRADE` still unlocks nothing

Brief §4's invariant is preserved and now mechanically checked.

- **No route requires `LIVE_TRADE`.** The guard fails if one does.
- A principal holding **only** `LIVE_TRADE` is tested against every route in the
  policy table and reaches none.
- `oipulse/trading/brokers/capability.py` is untouched: `LIVE_EXECUTION_ENABLED` is
  still a `False` module constant, `UpstoxBrokerAdapter` still declares an empty
  capability set, and `check_live_execution_barrier.py` still passes.
- The terminal's `liveTradingRenderable()` still returns the literal `false`.

```
LIVE_TRADE permission  ≠  live broker capability
```

---

## 20. CSRF

`17-SECURITY.md` §7's three controls, on `POST`/`PUT`/`PATCH`/`DELETE`:

1. **`SameSite=Lax`** — set where the cookie is issued. It cannot be *checked* on an
   incoming request, because a browser does not report which policy it applied; its
   consequence is enforced instead: `SAFE_METHODS` and `STATE_CHANGING_METHODS` are
   disjoint and the guard asserts it, so no state change can occur on `GET`.
2. **Origin / Referer validation** — must match the allow-list, and **absence is a
   refusal**, not a skip. That clause is the easiest one to implement backwards, and
   it has its own test.
3. **Double-submit token** — issued per session, compared against
   `identity_sessions.csrf_token` with `hmac.compare_digest`.

Origin is checked **before** the token, so a cross-origin refusal does not depend on
whether the token happened to be right. A session with no stored token refuses rather
than comparing two empty strings. API-key principals are exempt per §7 — the kind is
modelled and tested, though no API key can be issued.

`ALLOWED_ORIGINS` is now a validated setting. It is **required in production** and,
when unset elsewhere, warns that every state-changing request will be refused — an
unconfigured deployment accepts no writes from anywhere.

All 26 CSRF cases from the brief's §5 list are covered, plus the gate-level
interaction: an anonymous cross-site write fails on authentication first.

---

## 21. Migration 0012

`identity_sessions`, `17` §3's record field for field, plus `csrf_token` which §7.3
requires to live on the session.

- Primary key is the opaque id, because the cookie carries only it and every lookup
  is by it.
- Three check constraints: expiry after creation, revocation not before creation, and
  a 32-character floor on the token.
- Two indexes: `(user_id, created_at)` for "log out everywhere", `expires_at` for the
  sweep.
- `permissions_snapshot` is a `text[]` defaulting to empty — an unreadable snapshot
  grants nothing.
- Downgrade drops the table and its indexes in reverse order. Safe here for a specific
  reason: a session is ephemeral and re-creatable by signing in again, so dropping it
  logs everyone out, which is the correct consequence of removing the mechanism.
- Chain is linear: **14 revisions, one head**, `0011 → 0012`.
- Rows are retained after expiry and revocation: §9 audits revocation, and a revoked
  session whose row is gone cannot be told from one that never existed.

**Not applied.** No PostgreSQL and no `alembic` in this environment; the migration has
never executed, exactly as 0001–0011 have not. CI applies the chain against a real
PostgreSQL and fails on a skip.

---

## 22. Journal — the decision, with the trace

The brief asked which of four cases applies and said not to preserve the omission
merely because no route existed. Tracing it properly changed the answer and also found
something the first pass had wrong.

**Case D**, with a correction. What was found:

1. `12-API_SPEC.md` §3 specifies `/journal` — "CRUD on entries, linkable to signals,
   intents and trades". `13-FRONTEND_IA.md` §2/§6 and `18-ROADMAP.md` Phase 12 all
   list the screen. So it is required, not deferred.
2. `journal_entries` exists: migration 0008 creates it with `entry_type`,
   `cash_delta`, `realized_pnl_delta`, `fees_delta`, `cash_after`, `order_id`,
   `fill_key`, `source_event_key`.
3. **Nothing in `oipulse/` writes to it.** Phase 8 declared the table and shipped no
   writer; the paper ledger holds cash in memory. The first report said "no read
   route", which was true and incomplete.
4. **`13` §6 describes a different object.** "Supports revisiting whether the original
   hypothesis was correct" is free-text reflection. No table in the repository has a
   column for it.

**What was implemented:** the read half of the specified contract —
`GET /journal/entries` (filter by type, order and period; cursor pagination) and
`GET /journal/entries/{entry_id}` — and a real Journal screen at `/terminal/journal`
that uses it, renders actual responses, and handles loading, empty, error, unavailable
and degraded states. It is not a placeholder: it makes real requests and renders real
data.

**What was not implemented, and why:**

- **Writes.** §3 says CRUD. `journal_entries` carries a `source_event_key`, so entries
  are derived from events rather than authored, and no phase specifies an authoring
  path. Adding one would be a domain concept invented in a presentation phase.
- **Hypothesis notes.** No schema, no writer, no phase. Not invented. The screen says
  so on its face.

**The empty page is two sentences, not one.** `meta.availability` distinguishes
`NO_ENTRIES_RECORDED` (a fact about the account) from `NO_WRITER_IMPLEMENTED` (a fact
about the system), and the screen renders a different message for each, because an
operator who read the second as the first would conclude their account had been quiet.
`tests/phase12/test_journal_contract.py` asserts `JOURNAL_WRITER_IMPLEMENTED` still
matches the code by AST-scanning for an insert — when a writer lands, the test fails
and the UI notice has to come down with it.

**Screen count.** With Journal shipping, all **fourteen** names in `13` §2 are built.
The roadmap's "twelve workflow screens" contradicts its own fourteen-name deliverable
line; that contradiction is in the document and is not resolved here. The freeze's
testable condition — "none ahead of its backend" — now holds for every screen, and
`WITHHELD_SCREENS` is empty.

---

## 23. Charting

`13-FRONTEND_IA.md` §8 names the technology: "Charts via a single library
(`lightweight-charts` for time series where it fits...)". So charting **is** required
and the declared dependency **is** the specified one. It is now used rather than
removed.

`components/terminal/TimeSeriesChart.tsx` creates a real chart with zoom, pan, a
crosshair and a tooltip, wired into the three analytics screens (feature series) and
Backtest (equity curve). No second charting library was added.

**Temporal semantics (brief §10).** A time axis can carry one series of instants, so
it carries **market time**, and it is labelled `HORIZONTAL AXIS: MARKET TIME (IST)`
rather than "time". **Knowledge time** is fixed for a whole series — the API answers
one `(market_time, knowledge_time)` question per request — so it sits in the caption
and is repeated in every tooltip. Neither is collapsed into a generic timestamp.

**Missing, stale, unreliable.** A point with no value is emitted as *whitespace* and
the line breaks at it; nothing is interpolated. This is the sharpest form of the
`missing → 0` prohibition, because a line drawn through a gap does not look like
missing data — it looks like a measurement. The gap count is shown in the caption.
An `UNRELIABLE` or `UNKNOWN` state **withholds the plot entirely** rather than
desaturating it: a faint line is still a shape and a shape is still read as a trend.

The mapping is in `lib/terminal/screens/chartData.ts`, pure and covered by 13 tests
that run here. **The drawing has never executed** — `lightweight-charts` cannot be
installed — and that is the honest limit of this item.

---

## 24. Frontend build gates — still blocked, re-verified

Checked again this pass, not assumed from last time:

```
$ node --version          v24.20.0
$ npm --version           11.19.0
$ npm cache verify        npm error rofs EROFS: read-only file system
$ npm view next version   npm error 403 Forbidden - GET https://registry.npmjs.org/next
                          deny network-outbound registry.npmjs.org:443 (user denied)
```

The registry is denied by the sandbox proxy and the npm cache is read-only, so no
package can be installed by any route. `tsc --noEmit`, `next lint`, `next build` and
component rendering therefore **did not run and are not claimed**.

A stale, half-populated `frontend/node_modules` left by an aborted offline install
(359 empty scaffolding directories, no `typescript`, no `next`, no `react`) was
removed, because it is a trap for whoever runs this next.

**Not claimed:** type safety from Node's type stripping, or production readiness from
pure-module tests. Node strips types without checking them; the 228 terminal tests
cover decisions, not types and not rendering.

**What is claimed, and was run:** `tools/check_terminal_imports.py` resolves all local
imports and every named import against real export lists, which is the part of `tsc`
that catches a mistyped path or a renamed export. It is not a type check.

The CI path is correct and now runs eight gates plus a frontend job
(`npm ci`, `npm test`, `npm run type-check`, `npm run lint`, `npm run build`).

---

## 25. Security regression

The terminal's boundary is unchanged and re-verified by guard: no database, cache,
ORM, provider or broker import; no `process.env` anywhere in the Phase 12 tree; no
`EventSource`; no live path; no analytics recomputation; no Phase 13 reference.

The new auth work **adds nothing to the client bundle**. The session cookie is
`HttpOnly` and never read by script. The CSRF cookie is readable by design — a double
submit requires the client to echo it — and is not a credential alone: it is accepted
only alongside the session cookie and compared against the server's record. The
backend address remains a server-only variable with no `NEXT_PUBLIC_` prefix.

A new import contract, `identity-is-pure`, forbids `oipulse/identity/` from importing
any web stack, database driver or the clock — 16 contracts now.

### A defect this pass caught, and the guard added for its class

`oipulse/api/security.py` imported `utcnow` from `oipulse.core.clock`, which exports
`utc`, `ensure_utc` and a `Clock` protocol and has never had a `utcnow`. Nothing here
would have found it: the module needs `fastapi` to import, `compileall` compiles
without resolving names, and the other guards are lexical. It would have failed in
CI, at import, on a module that cannot be run locally.

Fixed by injecting a `Clock` — which is what `00-OVERVIEW.md` §5 wanted anyway, and
which makes "this session expired one second ago" an argument rather than a sleep.

`tools/check_internal_imports.py` now resolves every `from oipulse.… import name`
against the module it names, across the whole package. It abstains where a `*`
re-export makes the set open, rather than producing a false accusation. Five mutation
tests cover it, including one asserting that a submodule import is **not** flagged.

### A second defect, found while pushing

The generated contract's `source_digest` hashed raw file bytes. This repository has
`core.autocrlf=true` and no `.gitattributes`, so a checkout rewrites every `.py` file
to CRLF while git's stored blobs stay LF — which made the digest a property of *how
the tree was checked out* rather than of its content. A rebase produced a tree where
`git diff` was empty and `export_api_contract.py --check` failed.

The digest now normalises line endings before hashing, so the same commit yields the
same digest on any checkout. A test converts every `.py` file in a copy of the
package to the opposite convention and asserts the digest is unchanged.

---

## 26. Tests added

| Suite | Tests | Runs here |
|---|---|---|
| `test_authentication.py` | 16 | yes |
| `test_authorization.py` | 27 | yes |
| `test_csrf.py` | 26 | yes |
| `test_journal_contract.py` | 8 | yes |
| `test_auth_guard_mutations.py` | 21 | yes |
| `test_http_tests_authenticate.py` | 4 | yes |
| `test_security_http.py` | 22 | **no — skipped, runs in CI** |
| `frontend/tests/journal.test.ts` | 8 | yes |
| `frontend/tests/chartData.test.ts` | 13 | yes |
| `frontend/tests/errors.test.ts` (added) | 3 | yes |

All 20 items on the brief's §7 list are covered. The HTTP suite is the "direct HTTP
behavior" half; it skips here because `fastapi` cannot be installed, and a skip is
reported as a skip and never as a pass.

### A regression the sandbox could not have shown

The Phase 3–7 HTTP suites build apps with `create_app` and called them anonymously.
With the gate installed, every one of those requests becomes a 401 — in CI, where
those suites actually run, and invisibly here, where they error on import.

They are **not weakened**: every assertion about status codes, envelopes and error
handling is unchanged. Their clients now carry a session, via `tests/_http_auth.py`,
because a client without one is no longer a client that reaches the route. The
alternative — a flag that disables the gate for tests — was rejected: a boundary
switchable off for a test suite is a boundary whose production behaviour nothing
exercises.

`test_http_tests_authenticate.py` checks this statically, here: every `TestClient`
built over a `create_app` app is matched by an `authenticate(...)`, and the helper is
asserted not to touch the middleware or any bypass.

---

## 27. Verification actually performed

| Check | Result |
|---|---|
| `python3 -m unittest discover -s tests -t .` | **Ran 1584** · 0 failures · 55 errors · 66 skipped |
| `npm test` (`node --test`) | **228 tests, 228 pass, 0 fail** |
| All architecture guards | **18/18 PASS** |
| `python3 tools/export_api_contract.py --check` | PASS — 84 routes, 78 models, 37 serializers |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 355 files already formatted |
| `python -m compileall -q oipulse tools tests` | clean |

Errors are 45 × `fastapi` + 10 × `google`, **unchanged from the pre-remediation
baseline in count and kind**. Skips rose 44 → 66: the 22 new HTTP security tests.
Test count rose 1457 → 1584 (+127).

**Not run** — unchanged from §12 and re-confirmed above: `pytest`, `mypy --strict`,
`lint-imports`, `alembic`, PostgreSQL integration, `npm ci`, `tsc --noEmit`,
`next lint`, `next build`, component rendering, accessibility tooling. All are in CI.

---

## 28. Known limitations of the remediation

1. **No session can be issued over HTTP**, so the terminal cannot currently be signed
   into. `17` §11 forbids carrying forward unauthenticated issuance and no phase
   specifies a login flow. Enforcement is complete; issuance is a server-side function
   awaiting a specified flow. **This gates any deployment.**
2. **API keys are not implemented.** `17` §3 specifies them; the principal kind exists
   so the CSRF exemption is expressible and tested, and nothing issues one.
3. **No audit table.** `17` §9 specifies `audit_*` for login, revocation, permission
   change, kill-switch and order submission. Refusals are logged structurally; a
   durable append-only audit store is not part of this remediation and remains absent.
4. **`last_seen_at` is written on every allowed request.** Correct for the idle
   timeout, and a write per request against a real store. No batching or throttling is
   implemented.
5. **Rate limiting** (`17` §8) and **security headers / CSP** (`17` §7) are not
   implemented. Neither was named as a blocker; both remain absent.
6. **The durable session store is not written.** `identity_sessions` and the migration
   exist; the only implementation is `InMemorySessionStore`. A process without a store
   authenticates nobody and serves only the two probes — the safe failure, but not a
   working deployment.
7. **The chart has never rendered** (§23), and **no component has ever rendered**
   (§24).
8. **Journal shows an accounting journal over a table with no writer** (§22), and the
   hypothesis-note half of `13` §6 is absent.
9. **The verifier's commit was unreachable** (§17 preamble), so anything on it is
   unaddressed.
10. **Guards remain lexical**, not type-aware.
