"""FastAPI application factory."""
from __future__ import annotations

import logging
import time
from collections import defaultdict

import structlog
from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import auth, collector, oi, settings, stream
from app.core.config import get_settings

logger = structlog.get_logger(__name__)


def create_app() -> FastAPI:
    app_settings = get_settings()

    app = FastAPI(
        title="OI Pulse API",
        version="1.0.0",
        docs_url="/api/docs" if not app_settings.is_production else None,
        redoc_url="/api/redoc" if not app_settings.is_production else None,
    )

    # ── CORS ──────────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Simple per-IP rate limiter (Redis-free fallback for dev) ──────────────
    _counts: dict[str, list[float]] = defaultdict(list)
    _WINDOW = 60.0
    _LIMIT = 120

    @app.middleware("http")
    async def rate_limit(request: Request, call_next):
        ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        window_start = now - _WINDOW
        hits = [t for t in _counts[ip] if t > window_start]
        hits.append(now)
        _counts[ip] = hits
        if len(hits) > _LIMIT:
            return Response(
                content='{"detail":"Rate limit exceeded"}',
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                media_type="application/json",
                headers={"Retry-After": "60"},
            )
        return await call_next(request)

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(auth.router)
    app.include_router(oi.router)
    app.include_router(stream.router)
    app.include_router(collector.router)
    app.include_router(settings.router)

    # ── Mount V2 API (Phases 1-12) ────────────────────────────────────────────
    try:
        from oipulse.core.config import load_settings as load_v2_settings
        from oipulse.api.app import create_app as create_v2_app
        from oipulse.api.security import InMemorySessionStore
        v2_settings = load_v2_settings()
        v2_app = create_v2_app(v2_settings)
        v2_session_store = InMemorySessionStore()
        v2_app.state.session_store = v2_session_store
        app.state.v2_session_store = v2_session_store
        app.mount("/api/v2", v2_app)
    except Exception as exc:
        logger.warning("Could not mount v2 API into main application", exc_info=exc)

    # ── Health ────────────────────────────────────────────────────────────────
    @app.get("/api/health", tags=["ops"])
    async def health():
        return {"status": "ok"}

    # ── Global error handler ──────────────────────────────────────────────────
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.error("Unhandled error", path=request.url.path, exc_info=exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error"},
        )

    return app


app = create_app()
