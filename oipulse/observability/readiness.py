"""Readiness probes for the process's own dependencies.

`docs/design/16-OBSERVABILITY.md` §5 and `14-DEPLOYMENT.md` §3.

Readiness answers one question: *can this process do its job right now?* Three things
have to be true before the answer is yes, and each is probed separately so a failure
names its own cause instead of collapsing into a single red light:

* **PostgreSQL** is the source of durable truth. A process that cannot reach it cannot
  serve a point-in-time query or persist an observation.
* **Redis** is the coordination and live-read mechanism. Unreachable Redis does not
  corrupt anything, but it does mean coordination is degraded.
* **Local runtime dependencies** — the packages the selected role actually needs. A
  container built without the web stack, or with a driver missing, should fail readiness
  at once rather than at the first request.

**Provider market-data availability is deliberately NOT a readiness dependency.** The
approved design does not make it one, and making it one would take the whole API out of
rotation whenever Upstox has an outage or the market is closed — which is most of the
day. Feed health is reported as a data-quality and metrics concern, where an operator
can see it without traffic being withdrawn.

Every probe is bounded by a timeout and converts failure into `ok=False` with a reason.
A probe that raises would make `/ops/ready` return 500 and lose the diagnostic, so none
of them do.

Imports of `sqlalchemy` and `redis` are inside the probe functions on purpose: this
module must import on a bare interpreter so the probes can be unit-tested, and so a
missing driver is *reported by* the runtime-dependency probe rather than crashing it.
"""

from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from oipulse.core.config import Settings
from oipulse.observability.logging import get_logger

__all__ = [
    "DEFAULT_PROBE_TIMEOUT",
    "ROLE_REQUIREMENTS",
    "ProbeRegistry",
    "ProbeResult",
    "check_postgres",
    "check_redis",
    "check_runtime_dependencies",
    "register_dependency_probes",
]

log = get_logger(__name__)

#: Short, because readiness is polled often and a slow probe is itself a failure signal.
DEFAULT_PROBE_TIMEOUT = 2.0

#: Import names each role needs locally. Checked by import *spec*, not by importing:
#: importing a web framework inside a readiness probe is a side effect nobody asked for.
ROLE_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "api": ("fastapi", "uvicorn", "sqlalchemy", "asyncpg", "redis"),
    "ingestor": ("sqlalchemy", "asyncpg", "redis", "websockets"),
    "processor": ("sqlalchemy", "asyncpg", "redis"),
    "jobs": ("sqlalchemy", "asyncpg", "redis", "alembic"),
    "trader": ("sqlalchemy", "asyncpg", "redis"),
    "all": ("fastapi", "uvicorn", "sqlalchemy", "asyncpg", "redis"),
}


class ProbeRegistry(Protocol):
    """The slice of `api.health.ReadinessRegistry` this module needs.

    Structural rather than imported: `observability` must not depend on `api`, and the
    layering guard enforces that direction. Declaring the shape keeps the call
    type-checked without inverting the dependency or suppressing the error.
    """

    def register(self, name: str, probe: Callable[[], Awaitable[bool]]) -> None: ...


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """Outcome of one probe. `detail` is always populated on failure."""

    name: str
    ok: bool
    detail: str = ""

    def __bool__(self) -> bool:
        return self.ok


async def check_postgres(
    database_url: str, *, timeout: float = DEFAULT_PROBE_TIMEOUT
) -> ProbeResult:
    """`SELECT 1` against the configured database.

    A connection that opens but cannot execute is not readiness; the round trip is the
    check. Uses the async engine because `asyncpg` is the project's only driver.
    """
    name = "postgres"
    try:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
    except ImportError as exc:  # pragma: no cover - environment dependent
        return ProbeResult(name, False, f"sqlalchemy unavailable: {exc}")

    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:

        async def probe() -> None:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))

        await asyncio.wait_for(probe(), timeout=timeout)
        return ProbeResult(name, True)
    except TimeoutError:
        return ProbeResult(name, False, f"no response within {timeout}s")
    except Exception as exc:
        # Truncated and never including the URL: it carries the password.
        return ProbeResult(name, False, f"{type(exc).__name__}: {str(exc)[:200]}")
    finally:
        await engine.dispose()


async def check_redis(redis_url: str, *, timeout: float = DEFAULT_PROBE_TIMEOUT) -> ProbeResult:
    """`PING` against the configured Redis."""
    name = "redis"
    try:
        import redis.asyncio as aioredis
    except ImportError as exc:  # pragma: no cover - environment dependent
        return ProbeResult(name, False, f"redis client unavailable: {exc}")

    client = aioredis.from_url(redis_url)
    try:
        await asyncio.wait_for(client.ping(), timeout=timeout)
        return ProbeResult(name, True)
    except TimeoutError:
        return ProbeResult(name, False, f"no response within {timeout}s")
    except Exception as exc:
        return ProbeResult(name, False, f"{type(exc).__name__}: {str(exc)[:200]}")
    finally:
        await client.aclose()


def check_runtime_dependencies(role: str) -> ProbeResult:
    """Are the packages this role needs importable?

    Synchronous and I/O-free: it resolves import specs rather than importing, so the
    probe cannot execute third-party module-level code as a side effect of a health
    check. An unknown role is a failure, not a silent pass -- a typo in `--role` must
    not produce a process that reports itself ready while doing nothing.
    """
    name = "runtime_dependencies"
    required: Sequence[str] | None = ROLE_REQUIREMENTS.get(role)
    if required is None:
        return ProbeResult(name, False, f"unknown role {role!r}")

    missing = [module for module in required if importlib.util.find_spec(module) is None]
    if missing:
        return ProbeResult(
            name,
            False,
            f"role {role!r} requires {', '.join(missing)}; install with `pip install -e .`",
        )
    return ProbeResult(name, True)


def register_dependency_probes(
    registry: ProbeRegistry, settings: Settings, role: str
) -> tuple[str, ...]:
    """Attach the dependency probes to a `ReadinessRegistry`.

    Returns the names registered, so a caller (and a test) can assert what readiness
    actually covers rather than trusting that wiring happened.

    The registry is typed structurally (`ProbeRegistry`) because `observability` must
    not import `api`, which is where the concrete registry lives.
    """
    database_url = settings.database_url
    redis_url = settings.redis_url

    async def postgres() -> bool:
        return (await check_postgres(database_url)).ok

    async def redis_probe() -> bool:
        return (await check_redis(redis_url)).ok

    async def runtime() -> bool:
        return check_runtime_dependencies(role).ok

    registry.register("postgres", postgres)
    registry.register("redis", redis_probe)
    registry.register("runtime_dependencies", runtime)
    return ("postgres", "redis", "runtime_dependencies")
