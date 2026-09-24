"""FastAPI application factory.

`docs/design/14-DEPLOYMENT.md` §1 and `16-OBSERVABILITY.md` §5.

Phase 1 scope was health and readiness. Phase 3 adds `/market/state`, which serves a
deterministic `MarketState` reconstruction: read-only, and sharing one implementation
with the live assembly path rather than having a parallel historical one.

`api` still performs no business logic — no analytics, no strategy evaluation, no
trading decisions. Requesting a deterministic state reconstruction is explicitly
permitted (`14-DEPLOYMENT.md` §1); computing a metric from it is not, and does not
happen here.

A process serving `/market/state` must have `app.state.state_service` set to a
`StateService`. When it is absent the endpoint answers 503 rather than assembling a
state from a default it invented.

Requires `fastapi`. `oipulse.run` imports this module **lazily**, only when the `api`
role is actually started, so configuration validation and role dispatch remain usable on
an interpreter without the web stack installed.
"""

from __future__ import annotations

from fastapi import FastAPI

from oipulse.api.alerts import router as alerts_router
from oipulse.api.backtest import router as backtest_router
from oipulse.api.features import router as features_router
from oipulse.api.health import registry, router
from oipulse.api.market_state import router as market_router
from oipulse.api.replay import router as replay_router
from oipulse.api.research import router as research_router
from oipulse.api.signals import router as signals_router
from oipulse.core.config import Settings
from oipulse.observability.logging import get_logger
from oipulse.observability.readiness import register_dependency_probes

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
    # Phase 3. `/market/state` serves an assembled MarketState; the endpoint parses and
    # delegates, and holds no construction path of its own -- the single `build_state`
    # is what keeps live assembly and historical reconstruction from drifting apart
    # (`04-MARKETSTATE.md` §5).
    app.include_router(market_router)
    # Phase 4. Serving the registry is what makes conventions never implicit: a user
    # hovering GEX sees the dealer convention in force rather than reading the source.
    app.include_router(features_router)
    # Phase 5. Signals and alerts are separate routers because they are separate
    # concerns: a signal exists whether or not anyone is listening, and no alert
    # endpoint mutates signal truth.
    app.include_router(signals_router)
    app.include_router(alerts_router)
    # Phase 6. Research reads history; `knowledge_time` is required on any query that
    # does, because silently answering with latest knowledge would turn a
    # point-in-time question into a hindsight answer with no indication.
    app.include_router(research_router)
    # Phase 7. Replay reconstructs history through the same builder live uses, and
    # backtesting is that replay with a strategy attached. Neither router exposes an
    # order verb: replay's control surface is play/pause/step/seek/speed, and the
    # backtest surface reports simulated results. Live trading remains unimplemented
    # and, when it arrives, is gated separately (`12` §199).
    app.include_router(replay_router)
    app.include_router(backtest_router)

    # Readiness covers this process's own dependencies: PostgreSQL (durable truth),
    # Redis (coordination) and the packages the role needs locally. Phase 1 registered
    # nothing, so /ops/ready answered "ready" with an empty check set — technically
    # honest then, but it meant an API with an unreachable database still took traffic.
    #
    # Provider market-data availability is deliberately absent. The approved design does
    # not make it a readiness dependency, and if it were, every Upstox outage — and
    # every closed market — would withdraw the whole API from rotation.
    app.state.settings = settings
    registry.reset()
    app.state.readiness = registry
    app.state.readiness_checks = register_dependency_probes(registry, settings, settings.role)

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
