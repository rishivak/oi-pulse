# Phase 12 — Professional Terminal: implementation report

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
tests/phase12/                  53 tests, of which 23 are guard mutation tests
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
| Python suite | `python3 -m unittest discover -s tests -t .` | **Ran 1445** · 0 failures · 55 errors · 44 skipped |
| Terminal suite | `npm test` (`node --test`, no packages) | **204 tests, 204 pass, 0 fail** |
| Guards | each `tools/check_*.py` | **15/15 PASS** |
| Contract currency | `python3 tools/export_api_contract.py --check` | PASS — 82 routes, 77 models, 35 serializers |
| Lint | `ruff check .` | All checks passed |
| Format | `ruff format --check .` | 333 files already formatted |
| Bytecode | `python -m compileall -q oipulse tools tests` | clean |

Errors are 45 × `No module named 'fastapi'` and 10 × `No module named 'google'` —
**identical in count and kind to the baseline**, with no error of any other type. The
1392 → 1445 delta is exactly the 53 tests in `tests/phase12`; no pre-existing test was
added to, removed, weakened or skipped.

### Mutation-tested guards

Brief §30 asks for guards; the Phase 10 brief §29 warns that "safety guard PASS" is not
"mutation-tested safety". **23 of the 53 Phase 12 tests break exactly one thing in a
disposable copy of the tree and assert the guard names it**, and three more establish that
both guards pass on an untouched copy — so a later failure is attributable to the mutation
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
10. **Guards are lexical, not type-aware.** They strip comments and strings before matching,
    so they read code rather than prose, but a check here proves a token is absent — not
    that a behaviour is impossible. `tsc` and `eslint` remain authoritative and run in CI.

---

## 14. Remaining risks

1. **The build may fail on first CI run.** Nothing here has been compiled. The likeliest
   causes are a TypeScript type error in a `.tsx` file and a Next.js server/client
   boundary issue. Mitigations applied: the segment is `force-dynamic` because every screen
   reads `useSearchParams`; a guard checks that every file using a hook or an event handler
   declares `"use client"`; the route group bug was found and fixed. None of that is a
   substitute for running the build.
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
