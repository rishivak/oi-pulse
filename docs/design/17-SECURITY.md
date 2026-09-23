# OI Pulse v2 — Security Model

> **Deliverable W.** Broker credentials stay server-side. Permissions separate reading
> market data from paper trading from live trading, even with one user (brief §27).

---

## 1. Threat model

What this system protects:

| Asset | Threat | Consequence |
|---|---|---|
| Upstox access token | exfiltration | **unauthorized trading in a real account** |
| Upstox client secret | exfiltration | impersonation of the application |
| Session credential | theft, replay | account takeover |
| Live-trading capability | unauthorized or accidental use | financial loss |
| Observation history | loss | irreplaceable — cannot be re-fetched |
| Decision artifacts | tampering | audit chain destroyed |

Highest-severity risk is not data disclosure — it is **an attacker placing real orders**.
The controls below are weighted accordingly.

---

## 2. Credential handling

### Broker tokens
- Obtained server-side via OAuth 2.0; **never** transit to the browser, in any form.
- Encrypted at rest using **authenticated (integrity-protected) encryption via Fernet** —
  AES-128-CBC with an HMAC-SHA256 tag, encrypt-then-MAC — key from `TOKEN_ENCRYPTION_KEY`.
  Fernet is **not** an AEAD construction (it supports no associated data), and the earlier
  "AEAD" wording here was imprecise. Fernet is a sound choice for this purpose; only the
  description was wrong, and a wrong description leads someone to a wrong decision later.
- **Documented accurately, and the documentation is tested.** The legacy codebase claims
  "AES-256-GCM" while using Fernet. v2 states its construction correctly and a test
  asserts the stated algorithm is the one actually in use, so the two cannot drift.
- Decrypted only in-process at the moment of use; never logged, never cached in Redis,
  never placed in an exception message.
- Redacted structurally: the credential type has no `__str__`/`__repr__` that can emit the
  value, so an accidental log line cannot leak it.

### Application secrets
Environment-sourced, validated at startup. The process **refuses to start** on a
placeholder value, a short key, or a missing secret in production. Secrets never appear in
version control; `.env.example` carries generation instructions and no values.

### Key rotation
`TOKEN_ENCRYPTION_KEY` supports a key-id prefix on ciphertext so rotation re-encrypts
progressively rather than requiring a flag day.

---

## 3. Authentication

### Sessions — server-side and revocable
The legacy design signs the literal string `user:{id}` and stores nothing, so logout
clears a cookie while the signed value remains valid until expiry. There is no revocation
and no "log out everywhere".

v2 stores a real session record:

```
identity_sessions
  id (opaque, random)  user_id  created_at  last_seen_at  expires_at
  revoked_at  user_agent  ip  permissions_snapshot
```

The cookie carries only the opaque id. Server-side lookup means revocation is immediate
and per-session. Cookie flags: `HttpOnly`, `Secure` (enforced in production), `SameSite=Lax`, host-scoped,
with a documented TTL and idle timeout. CSRF defence is in §7 — `SameSite` alone is not
treated as sufficient.

### API keys
For programmatic access: hashed at rest, scoped by permission, independently revocable,
never granted `LIVE_TRADE` without an explicit separate step.

---

## 4. Authorization

Permissions are separated **even with a single user**, per the brief:

| Permission | Grants |
|---|---|
| `MARKET_DATA_READ` | market state, analytics, signals |
| `RESEARCH` | studies, datasets, backtests, replay |
| `PAPER_TRADE` | paper accounts, intents, orders |
| `LIVE_TRADE` | live accounts — **separate, never implied by `PAPER_TRADE`** |
| `ADMIN` | universes, retention, operational endpoints |

Enforced at the API boundary and re-checked in the trading domain — an intent targeting a
`LIVE` account is rejected without `LIVE_TRADE` regardless of how it arrived. Defence in
depth here is warranted because the failure mode is real money.

### Live-trading gate
Three independent conditions, all required:
1. `LIVE_TRADING_ENABLED` feature flag,
2. a second explicit confirmation environment variable,
3. the `LIVE_TRADE` permission on the principal.

Any one absent means live orders cannot be placed. The UI does not render live trading at
all unless all three hold.

---

## 5. The market-data / trading boundary

An architectural control, not merely a policy one:

- `ingestor`, `processor` and `jobs` roles **hold no order authority** and have no broker
  write credentials. They can read market data and nothing else.
- Only `trader` holds broker write capability, and it is the only role that can be
  disabled entirely.
- Database roles mirror this: the ingestion role has no write grant on `trade_*`.

Compromising the data pipeline therefore does not yield order-placement capability.

---

## 6. Input and data handling

- All input validated by Pydantic models at the boundary; no raw request data reaches a
  service.
- Parameterized queries throughout (SQLAlchemy); no string-built SQL.
- Observation tables have no `UPDATE`/`DELETE` grant for the application role — immutability
  is enforced by the database, not by discipline.
- Decision artifacts likewise append-only.
- Vendor payloads are validated before persistence; unknown fields preserved in
  `raw_extra` rather than executed or trusted.

---

## 7. Transport and network

TLS everywhere, including to Upstox. Certificate verification never disabled, in any
environment. Same-origin API access through the reverse proxy so cookies stay host-scoped;
CORS restricted to configured origins with credentials enabled only for those.

Security headers: HSTS, `X-Content-Type-Options`, `X-Frame-Options: DENY`,
`Referrer-Policy`, and a Content-Security-Policy that disallows inline script.

### CSRF protection

Browser authentication uses cookies, so **every state-changing request needs explicit CSRF
defence**. The OAuth `state` parameter protects the OAuth flow only; it does nothing for an
authenticated session's ordinary requests, and treating it as general CSRF coverage would
be a mistake.

Defence in depth, all three required on state-changing methods
(`POST`/`PUT`/`PATCH`/`DELETE`):

1. **`SameSite=Lax`** on the session cookie — blocks the common cross-site form and
   navigation cases. Necessary, not sufficient: it does not cover same-site subdomain
   attacks and its top-level-`GET` exemption means no state change may ever occur on `GET`.
2. **Origin / Referer validation** — the request's `Origin` must match an allow-listed
   origin. Rejected when absent on a state-changing request rather than allowed through.
3. **Double-submit CSRF token** — issued per session, sent in a header the browser cannot
   set cross-origin, compared against the session record server-side.

API-key principals are exempt from CSRF (no ambient credential to abuse) but remain subject
to origin checks where a browser is involved.

**Order placement carries the strictest form:** token plus re-authentication for live
accounts, because the failure mode is an attacker moving real money.

---

## 8. Rate limiting and abuse

Redis-backed, **per principal** rather than per IP, applied at the API boundary. The
legacy in-process per-IP `defaultdict` neither holds across replicas nor evicts idle
entries, and is being replaced rather than carried forward.

Stricter limits on authentication endpoints. Order-placement endpoints carry their own
limit independent of the general API budget, so a runaway client cannot exhaust order
capacity.

---

## 9. Audit trail

`audit_*` records every security-relevant action: login, logout, session revocation, OAuth
grant and refresh, permission change, credential creation and rotation, live-trading
enablement, kill-switch activation, risk-limit modification, and every order submission.

Properties: append-only (no `UPDATE`/`DELETE` grant), **never contains token values**,
retained indefinitely, and independent of the logging pipeline — a dropped log line must
never lose an audit record.

---

## 10. Dependency and supply chain

Pinned dependencies with hashes; automated vulnerability audit in CI; container base
images pinned by digest and rebuilt on advisory; images run as non-root with a read-only
root filesystem where practical.

---

## 11. Known accepted risks

Stated explicitly rather than left implicit:

| Risk | Status |
|---|---|
| Single-tenant deployment | Multi-tenant isolation is designed for but untested until there is a second user |
| Broker-side duplicate prevention not guaranteed | Mitigated by UNKNOWN → reconciliation (`11-TRADING.md` §5), not eliminated |
| Operator with `ADMIN` can enable live trading | Accepted; audited, and requires the environment gate as well |
| Encryption key compromise exposes stored tokens | Mitigated by rotation support and by tokens being short-lived; not eliminated |
| Observation history is irreplaceable | Mitigated by WAL archiving and tested restores (`14-DEPLOYMENT.md` §7) |

The legacy `/auth/offline-session` endpoint — unauthenticated, and issuing a session for
whichever user owns the newest snapshot — is **not carried forward in any form**. It is a
complete authentication bypass the moment a second user exists.
