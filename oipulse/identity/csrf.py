"""CSRF defence. `17-SECURITY.md` §7.

The specification is explicit that this is three controls, all required, and that
the first is not sufficient alone:

> Defence in depth, all three required on state-changing methods
> (`POST`/`PUT`/`PATCH`/`DELETE`):
>
> 1. **`SameSite=Lax`** on the session cookie — blocks the common cross-site form
>    and navigation cases. Necessary, not sufficient: it does not cover same-site
>    subdomain attacks and its top-level-`GET` exemption means no state change may
>    ever occur on `GET`.
> 2. **Origin / Referer validation** — the request's `Origin` must match an
>    allow-listed origin. Rejected when absent on a state-changing request rather
>    than allowed through.
> 3. **Double-submit CSRF token** — issued per session, sent in a header the browser
>    cannot set cross-origin, compared against the session record server-side.

Control 1 is a cookie attribute and is set where the cookie is issued
(`oipulse/api/security.py`); it cannot be checked on an incoming request, because a
browser does not tell the server which `SameSite` policy it applied. Controls 2 and
3 are evaluated here.

The `SameSite` clause has a consequence this module enforces rather than assumes:
**no state change may ever occur on `GET`.** `SAFE_METHODS` is the set exempt from
CSRF, and `tools/check_auth_boundary.py` asserts that no route policy allows a state
change on one of them.

### Why absent is rejected rather than skipped

> Rejected when absent on a state-changing request rather than allowed through.

Some clients omit `Origin`. Treating an omission as a pass makes the control
optional at the attacker's discretion, since a request that omits the header is
exactly what an attacker constructs. The trade is that a non-browser client must
send an `Origin`, or authenticate with an API key, which §7 exempts.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from enum import StrEnum

from oipulse.identity.principal import PrincipalKind

__all__ = [
    "CSRF_COOKIE",
    "CSRF_HEADER",
    "SAFE_METHODS",
    "STATE_CHANGING_METHODS",
    "CsrfOutcome",
    "CsrfResult",
    "evaluate_csrf",
]

#: A header the browser cannot set cross-origin without a preflight (`17` §7.3).
CSRF_HEADER = "x-oipulse-csrf"
#: The readable half of the double submit. Not `HttpOnly` — the client must echo it.
#: It is not a credential on its own: it is only accepted alongside the session
#: cookie, and it is compared against the server's record of that session.
CSRF_COOKIE = "oipulse_csrf"

#: `17` §7: the four methods the defence applies to.
STATE_CHANGING_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})
#: Exempt, and therefore forbidden from changing state at all.
SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})


class CsrfOutcome(StrEnum):
    """Why CSRF passed or failed."""

    #: A safe method. No defence applies, and none may be needed, because a safe
    #: method may not change state.
    NOT_REQUIRED = "NOT_REQUIRED"
    #: An API-key principal (`17` §7): no ambient credential to abuse.
    EXEMPT_API_KEY = "EXEMPT_API_KEY"
    PASSED = "PASSED"
    ORIGIN_MISSING = "ORIGIN_MISSING"
    ORIGIN_NOT_ALLOWED = "ORIGIN_NOT_ALLOWED"
    TOKEN_MISSING = "TOKEN_MISSING"
    TOKEN_MISMATCH = "TOKEN_MISMATCH"
    #: The session carries no server-side token, so nothing can be compared.
    TOKEN_UNAVAILABLE = "TOKEN_UNAVAILABLE"

    @property
    def is_allowed(self) -> bool:
        return self in (
            CsrfOutcome.NOT_REQUIRED,
            CsrfOutcome.EXEMPT_API_KEY,
            CsrfOutcome.PASSED,
        )


@dataclass(frozen=True, slots=True)
class CsrfResult:
    outcome: CsrfOutcome
    detail: str = ""

    @property
    def is_allowed(self) -> bool:
        return self.outcome.is_allowed


def _normalise(origin: str) -> str:
    """Compare origins case-insensitively on scheme and host, and without a path.

    An `Origin` header is a serialised origin — scheme, host, optional port — so a
    trailing slash or a path is malformed. Stripping rather than rejecting would let
    `https://evil.test/https://app.test` normalise into something that matched, so
    only the trailing slash is tolerated.
    """
    return origin.strip().rstrip("/").lower()


def evaluate_csrf(
    *,
    method: str,
    principal_kind: PrincipalKind,
    origin: str | None,
    referer: str | None,
    header_token: str | None,
    session_token: str,
    allowed_origins: frozenset[str],
) -> CsrfResult:
    """Controls 2 and 3 of `17` §7, together.

    Order matters for what an attacker learns: origin is checked before the token,
    so a cross-origin request is rejected without the response depending on whether
    the token happened to be right.
    """
    upper = method.upper()
    if upper not in STATE_CHANGING_METHODS:
        return CsrfResult(CsrfOutcome.NOT_REQUIRED)
    if principal_kind is PrincipalKind.API_KEY:
        return CsrfResult(
            CsrfOutcome.EXEMPT_API_KEY,
            "API-key principals carry no ambient credential (17-SECURITY.md §7)",
        )

    # ---- control 2: Origin, falling back to Referer as §7 allows ("Origin / Referer")
    candidate = origin
    source = "Origin"
    if candidate is None or candidate.strip() == "":
        candidate = _origin_of(referer)
        source = "Referer"
    if candidate is None or candidate.strip() == "":
        return CsrfResult(
            CsrfOutcome.ORIGIN_MISSING,
            "no Origin or Referer on a state-changing request; absence is rejected "
            "rather than allowed through (17-SECURITY.md §7.2)",
        )
    if _normalise(candidate) not in {_normalise(o) for o in allowed_origins}:
        return CsrfResult(
            CsrfOutcome.ORIGIN_NOT_ALLOWED,
            f"{source} is not an allow-listed origin",
        )

    # ---- control 3: double-submit token
    if not session_token:
        return CsrfResult(
            CsrfOutcome.TOKEN_UNAVAILABLE,
            "the session carries no CSRF token, so nothing can be compared; the "
            "request is refused rather than passed on an empty comparison",
        )
    if header_token is None or header_token == "":
        return CsrfResult(CsrfOutcome.TOKEN_MISSING, f"no {CSRF_HEADER} header")
    # Constant-time: a timing-distinguishable comparison leaks the token a byte at
    # a time, and the token is what stands between a session and a forged write.
    if not hmac.compare_digest(header_token, session_token):
        return CsrfResult(CsrfOutcome.TOKEN_MISMATCH, "CSRF token does not match the session")
    return CsrfResult(CsrfOutcome.PASSED)


def _origin_of(referer: str | None) -> str | None:
    """The scheme+host+port of a Referer, or `None` if it has none.

    Parsed by hand rather than with `urllib`: this runs on every state-changing
    request, and the only thing needed is the part before the third slash.
    """
    if referer is None:
        return None
    text = referer.strip()
    if "://" not in text:
        return None
    scheme, _, rest = text.partition("://")
    authority = rest.split("/", 1)[0]
    if authority == "":
        return None
    return f"{scheme}://{authority}"
