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
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from oipulse.core.config import Settings
from oipulse.observability.logging import get_logger

__all__ = [
    "DEFAULT_PROBE_TIMEOUT",
    "ROLE_REQUIREMENTS",
    "DependencyProbes",
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


class _RedisLike(Protocol):
    """The two operations this probe needs from a Redis client.

    `redis.asyncio.from_url` is untyped, so calling it directly from a strict-checked
    module yields an untyped call and an untyped result. Declaring the shape we use
    keeps the boundary typed without a blanket ignore and without pretending to know
    the rest of the client's surface.
    """

    async def ping(self) -> Any: ...
    async def aclose(self) -> None: ...


#: The one signature this module relies on from redis-py's untyped constructor.
_RedisFromUrl = Callable[[str], _RedisLike]


def _redis_client(redis_url: str) -> _RedisLike:
    """Typed boundary around the untyped `redis.asyncio.from_url` constructor.

    `from_url` carries no annotations, so calling it from a strict-checked module is a
    `no-untyped-call` *regardless of how the result is annotated* -- the error is about
    the call, not the assignment, which is why annotating the target did not silence
    it. Casting the **callable** rather than the result confines the assertion to one
    expression and writes down the exact signature we depend on, so a redis-py release
    that types `from_url` incompatibly surfaces here instead of somewhere downstream.

    `cast` is erased at runtime: the call is still `aioredis.from_url(redis_url)` with
    the same single positional argument, so connection behaviour is unchanged.
    """
    import redis.asyncio as aioredis

    from_url = cast(_RedisFromUrl, aioredis.from_url)
    return from_url(redis_url)


async def check_redis(redis_url: str, *, timeout: float = DEFAULT_PROBE_TIMEOUT) -> ProbeResult:
    """`PING` against the configured Redis."""
    name = "redis"
    try:
        client = _redis_client(redis_url)
    except ImportError as exc:  # pragma: no cover - environment dependent
        return ProbeResult(name, False, f"redis client unavailable: {exc}")

    try:
        await asyncio.wait_for(client.ping(), timeout=timeout)
        return ProbeResult(name, True)
    except TimeoutError:
        return ProbeResult(name, False, f"no response within {timeout}s")
    except Exception as exc:
        return ProbeResult(name, False, f"{type(exc).__name__}: {str(exc)[:200]}")
    finally:
        await client.aclose()


def check_runtime_dependencies(
    role: str, *, requirements: Mapping[str, Sequence[str]] | None = None
) -> ProbeResult:
    """Are the packages this role needs importable?

    Synchronous and I/O-free: it resolves import specs rather than importing, so the
    probe cannot execute third-party module-level code as a side effect of a health
    check. An unknown role is a failure, not a silent pass -- a typo in `--role` must
    not produce a process that reports itself ready while doing nothing.

    `requirements` overrides the role-to-packages table. It exists so a test can supply
    a package that is *definitely* absent and assert the detection path, instead of
    relying on which packages happen to be installed on the machine running the suite —
    an assumption that made this probe's test pass in a bare sandbox and fail on a
    provisioned one. Production always uses the default table.
    """
    name = "runtime_dependencies"
    table = ROLE_REQUIREMENTS if requirements is None else requirements
    required: Sequence[str] | None = table.get(role)
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


@dataclass(frozen=True, slots=True)
class DependencyProbes:
    """The three dependency probes, as an injectable bundle.

    Defaults are the real probes, so production behaviour is exactly what it was. The
    seam exists so a caller can make failure deterministic: the preflight tests
    previously asserted against whatever PostgreSQL and Redis happened to be running on
    the verifying machine, which is not a property of this system at all.
    """

    postgres: Callable[[str], Awaitable[ProbeResult]] = field(default=check_postgres)
    redis: Callable[[str], Awaitable[ProbeResult]] = field(default=check_redis)
    runtime: Callable[[str], ProbeResult] = field(default=check_runtime_dependencies)

    async def evaluate(self, *, database_url: str, redis_url: str, role: str) -> list[ProbeResult]:
        """Run **all three** probes and return every result.

        Aggregating rather than short-circuiting is deliberate and is the contract the
        preflight tests assert: an operator fixing a deployment should see the whole
        list in one restart, not discover the next broken dependency on the next try.
        """
        return [
            self.runtime(role),
            await self.postgres(database_url),
            await self.redis(redis_url),
        ]


def register_dependency_probes(
    registry: ProbeRegistry,
    settings: Settings,
    role: str,
    probes: DependencyProbes | None = None,
) -> tuple[str, ...]:
    """Attach the dependency probes to a `ReadinessRegistry`.

    Returns the names registered, so a caller (and a test) can assert what readiness
    actually covers rather than trusting that wiring happened.

    The registry is typed structurally (`ProbeRegistry`) because `observability` must
    not import `api`, which is where the concrete registry lives.
    """
    database_url = settings.database_url
    redis_url = settings.redis_url
    checks = probes or DependencyProbes()

    async def postgres() -> bool:
        return (await checks.postgres(database_url)).ok

    async def redis_probe() -> bool:
        return (await checks.redis(redis_url)).ok

    async def runtime() -> bool:
        return checks.runtime(role).ok

    registry.register("postgres", postgres)
    registry.register("redis", redis_probe)
    registry.register("runtime_dependencies", runtime)
    return ("postgres", "redis", "runtime_dependencies")
