"""Health and readiness.

`docs/design/16-OBSERVABILITY.md` §5. Liveness and readiness are distinct: a process can
be responsive while unable to do its job, and conflating them makes an orchestrator
restart a healthy process or route traffic to a useless one.

Role-specific readiness matters most for `trader`, which is **not ready until
reconciliation is clean** (`11-TRADING.md` §6). That check lands in Phase 10; the hook is
declared here so the contract is visible from the start rather than retrofitted.

`api` performs no business logic — no analytics, no strategy evaluation, no trading
decisions (`14-DEPLOYMENT.md` §1).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import APIRouter, Response, status

router = APIRouter(prefix="/ops", tags=["ops"])


@dataclass(frozen=True, slots=True)
class ReadinessCheck:
    name: str
    probe: Callable[[], Awaitable[bool]]


class ReadinessRegistry:
    """Role-specific readiness probes, registered at wiring time.

    Phase 1 ships the registry empty. Each later phase registers its own dependency —
    `ingestor` an authenticated WS, `processor` recent observations, `trader` a clean
    reconciliation — so readiness grows with capability instead of being a static list
    that drifts.
    """

    def __init__(self) -> None:
        self._checks: list[ReadinessCheck] = []

    def register(self, name: str, probe: Callable[[], Awaitable[bool]]) -> None:
        self._checks.append(ReadinessCheck(name, probe))

    async def evaluate(self) -> dict[str, bool]:
        return {check.name: await check.probe() for check in self._checks}


registry = ReadinessRegistry()


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness: the process is responsive. Deliberately does no dependency I/O."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(response: Response) -> dict[str, object]:
    """Readiness: dependencies reachable and role-specific preconditions met."""
    results = await registry.evaluate()
    ok = all(results.values())
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"ready": ok, "checks": results}
