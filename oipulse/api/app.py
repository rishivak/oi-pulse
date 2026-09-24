"""FastAPI application factory.

`docs/design/14-DEPLOYMENT.md` §1 and `16-OBSERVABILITY.md` §5.

Phase 1 scope: health and readiness only. `api` performs no business logic — no
analytics, no strategy evaluation, no trading decisions. It may later serve a
deterministic `MarketState` reconstruction, which is read-only and shares one
implementation with the live path; that arrives with Phase 3.

Requires `fastapi`. `oipulse.run` imports this module **lazily**, only when the `api`
role is actually started, so configuration validation and role dispatch remain usable on
an interpreter without the web stack installed.
"""

from __future__ import annotations

from fastapi import FastAPI

from oipulse.api.health import registry, router
from oipulse.core.config import Settings
from oipulse.observability.logging import get_logger

__all__ = ["create_app"]

log = get_logger(__name__)


def create_app(settings: Settings) -> FastAPI:
    """Build the ASGI application.

    Takes validated `Settings` rather than reading the environment itself: the process
    must refuse to start on bad configuration *before* anything is constructed
    (`14` §3), and a factory that loads its own config cannot be given a test one.
    """
    app = FastAPI(
        title="OI Pulse v2",
        version="2.0.0a1",
        # Interactive docs are disabled in production (`17-SECURITY.md`).
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
    )

    app.include_router(router)

    # Readiness probes are registered per role as capability lands: `ingestor` an
    # authenticated feed, `processor` recent observations, `trader` a clean
    # reconciliation. Phase 1 registers none, so /ops/ready reports ready with an empty
    # check set — honest, because there is nothing yet that could be unready.
    app.state.settings = settings
    app.state.readiness = registry

    @app.on_event("startup")
    async def _log_startup() -> None:
        log.info(
            "api_started",
            extra={
                "role": settings.role,
                "app_env": settings.app_env,
                "live_trading_env_gates_open": settings.live_trading_env_gates_open,
            },
        )

    return app
