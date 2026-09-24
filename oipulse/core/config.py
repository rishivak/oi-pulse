"""Configuration — environment-sourced, validated at startup.

`docs/design/14-DEPLOYMENT.md` §3: the process **refuses to start** on invalid
configuration rather than failing later. Startup validation rejects placeholder secrets,
short keys, and missing values required in production.

**Implementation note.** `14` §3 names `pydantic-settings`. This module is deliberately
stdlib-only so that `oipulse.core` carries *zero* third-party dependencies: `core` is the
innermost layer, imported by `analytics/*`, which the boundary contract forbids from
reaching a DB, HTTP client or settings library. Keeping `core` dependency-free makes that
contract structural rather than aspirational. The validation semantics required by `14`
are preserved exactly. Recorded as **AD-29** in `19-DECISIONS.md` (AD-28 is the
separate `platform` -> `oipulse` package rename).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from oipulse.core.errors import ConfigurationError

__all__ = [
    "PLACEHOLDER_MARKERS",
    "Environment",
    "Settings",
    "find_placeholder",
    "load_settings",
]

# Values that must never survive into a running process. The legacy codebase shipped
# `REPLACE_WITH_FERNET_KEY` as a literal default and guarded only that one string.
PLACEHOLDER_MARKERS: tuple[str, ...] = (
    "REPLACE_WITH",
    "CHANGE_ME",
    "CHANGEME",
    "YOUR_",
    "XXXX",
    "TODO",
    "PLACEHOLDER",
    "EXAMPLE",
    "<",
)

_MIN_SECRET_LENGTH = 32


class Environment(str):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


def find_placeholder(value: str) -> str | None:
    """Return the placeholder marker present in *value*, or None.

    Case-insensitive and substring-based: a secret is not made real by wrapping a
    placeholder in other characters.
    """
    upper = value.upper()
    for marker in PLACEHOLDER_MARKERS:
        if marker in upper:
            return marker
    return None


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated runtime configuration.

    Only the keys Phase 1 needs are present. `14` §3 lists the full set; later phases add
    their own groups as they are built, rather than declaring them unused now.
    """

    app_env: str
    role: str
    log_level: str
    instance_id: str

    database_url: str
    redis_url: str

    session_secret_key: str
    token_encryption_key: str

    # Live trading requires all three gates (17-SECURITY.md §4). Both env-sourced gates
    # default to off; the third is a per-principal permission checked at request time.
    live_trading_enabled: bool = False
    live_trading_confirmed: bool = False

    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_production(self) -> bool:
        return self.app_env == Environment.PRODUCTION

    @property
    def live_trading_env_gates_open(self) -> bool:
        """True only when *both* environment gates are set.

        Never sufficient on its own — `LIVE_TRADE` permission is the third gate and is
        evaluated per principal, not per process.
        """
        return self.live_trading_enabled and self.live_trading_confirmed


_TRUE = {"1", "true", "yes", "on"}
_VALID_ROLES = {"api", "ingestor", "processor", "trader", "jobs", "all"}
_VALID_ENVS = {
    Environment.DEVELOPMENT,
    Environment.STAGING,
    Environment.PRODUCTION,
}


def _require(env: Mapping[str, str], key: str, errors: list[str]) -> str:
    value = env.get(key, "").strip()
    if not value:
        errors.append(f"{key} is required and was empty or unset")
        return ""
    return value


def _require_secret(env: Mapping[str, str], key: str, errors: list[str]) -> str:
    value = _require(env, key, errors)
    if not value:
        return ""
    marker = find_placeholder(value)
    if marker is not None:
        errors.append(f"{key} contains placeholder text {marker!r}; set a real value")
    elif len(value) < _MIN_SECRET_LENGTH:
        errors.append(f"{key} is {len(value)} characters; at least {_MIN_SECRET_LENGTH} required")
    return value


def _flag(env: Mapping[str, str], key: str) -> bool:
    return env.get(key, "").strip().lower() in _TRUE


def load_settings(
    env: Mapping[str, str] | None = None,
    *,
    strict: bool = True,
) -> Settings:
    """Load and validate configuration.

    Every problem is collected and reported together — reporting only the first means an
    operator fixes one variable, restarts, and discovers the next.

    Raises `ConfigurationError` when validation fails. `strict=False` is for tooling that
    needs to inspect a partial configuration (for example the placeholder checker); it
    still collects errors but returns them as warnings.
    """
    env = os.environ if env is None else env
    errors: list[str] = []
    warnings: list[str] = []

    app_env = env.get("APP_ENV", Environment.DEVELOPMENT).strip().lower()
    if app_env not in _VALID_ENVS:
        errors.append(f"APP_ENV {app_env!r} is not one of {sorted(_VALID_ENVS)}")

    role = env.get("ROLE", "all").strip().lower()
    if role not in _VALID_ROLES:
        errors.append(f"ROLE {role!r} is not one of {sorted(_VALID_ROLES)}")

    database_url = _require(env, "DATABASE_URL", errors)
    redis_url = env.get("REDIS_URL", "redis://localhost:6379/0").strip()

    session_secret_key = _require_secret(env, "SESSION_SECRET_KEY", errors)
    token_encryption_key = _require_secret(env, "TOKEN_ENCRYPTION_KEY", errors)

    live_enabled = _flag(env, "LIVE_TRADING_ENABLED")
    live_confirmed = _flag(env, "LIVE_TRADING_CONFIRMED")

    # Both gates open is legitimate configuration but is never silent.
    if live_enabled and live_confirmed:
        warnings.append(
            "both live-trading environment gates are open; the LIVE_TRADE permission "
            "remains the third and final gate"
        )
    elif live_enabled:
        warnings.append(
            "LIVE_TRADING_ENABLED is set but LIVE_TRADING_CONFIRMED is not; "
            "live trading remains disabled"
        )

    if app_env == Environment.PRODUCTION:
        if database_url.startswith("sqlite"):
            errors.append("DATABASE_URL must not be sqlite in production")
        if _flag(env, "DEBUG"):
            errors.append("DEBUG must not be set in production")

    if errors:
        if strict:
            joined = "\n  - ".join(errors)
            raise ConfigurationError(f"configuration invalid; refusing to start:\n  - {joined}")
        warnings.extend(errors)

    return Settings(
        app_env=app_env,
        role=role,
        log_level=env.get("LOG_LEVEL", "info").strip().lower(),
        instance_id=env.get("INSTANCE_ID", "local").strip(),
        database_url=database_url,
        redis_url=redis_url,
        session_secret_key=session_secret_key,
        token_encryption_key=token_encryption_key,
        live_trading_enabled=live_enabled,
        live_trading_confirmed=live_confirmed,
        warnings=tuple(warnings),
    )
