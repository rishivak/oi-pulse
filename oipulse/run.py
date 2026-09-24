"""Process entrypoint.

`docs/design/14-DEPLOYMENT.md` §1. One image, one codebase; the role is selected at
startup:

    python -m oipulse.run --role api
    python -m oipulse.run --role ingestor      # Phase 2 runtime
    python -m oipulse.run --role processor     # Phase 3
    python -m oipulse.run --role trader        # feature-flagged, off by default
    python -m oipulse.run --role jobs
    python -m oipulse.run --role all           # development convenience

`--check` validates configuration and exits without starting anything. It is the
supported way to confirm a deployment's environment before the process takes traffic,
and it works on an interpreter with no web stack installed because the heavy imports
below are deliberately lazy.

**Why the imports are lazy.** Configuration must be validated and rejected *before* any
subsystem is constructed (`14` §3: the process refuses to start rather than failing
later). Importing FastAPI at module scope would also make `--check` and `--help`
unusable wherever the web stack is absent, which is exactly where an operator most wants
to check their configuration.
"""

from __future__ import annotations

import argparse
import sys

from oipulse.core.clock import SystemClock
from oipulse.core.config import Settings, load_settings
from oipulse.core.errors import ConfigurationError
from oipulse.observability.logging import configure_logging, get_logger

__all__ = ["main", "run_api", "run_ingestor_role"]

VALID_ROLES = ("api", "ingestor", "processor", "trader", "jobs", "all")

#: Roles whose runtime arrives in a later phase. Named individually so an operator gets
#: "not implemented until Phase 3" rather than a silent no-op or a stack trace.
#: `ingestor` is no longer here: its runtime is implemented in
#: `oipulse.marketdata.runtime` and dispatched below.
_PHASE_OF_ROLE = {
    "processor": "Phase 3",
    "trader": "Phase 8 onward; live execution stays disabled behind three gates",
    "jobs": "Phase 2",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m oipulse.run",
        description="OI Pulse v2 process entrypoint.",
    )
    parser.add_argument("--role", choices=VALID_ROLES, default="api")
    # Binds all interfaces because the process runs in a container behind nginx.
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate configuration and exit without starting the process",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="development autoreload; refused when APP_ENV=production",
    )
    return parser


def run_api(settings: Settings, host: str, port: int, reload: bool) -> int:
    """Start the HTTP server. Imports the web stack only at this point."""
    try:
        import uvicorn
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        print(
            f"cannot start the api role: {exc.name} is not installed.\n"
            f"Install the runtime dependencies with:  pip install -e .",
            file=sys.stderr,
        )
        return 3

    from oipulse.api.app import create_app

    if reload and settings.is_production:
        # The legacy service hardcoded reload=True and shipped it to every
        # environment (`14` §5). Refusing is better than warning.
        raise ConfigurationError("--reload must not be used when APP_ENV=production")

    uvicorn.run(create_app(settings), host=host, port=port, reload=False, log_config=None)
    return 0


def run_ingestor_role(settings: Settings) -> int:
    """Start the canonical collector. Imports the market-data stack only at this point.

    Lazy for the same reason as `run_api`: `--check` and `--help` must work on an
    interpreter without SQLAlchemy or a WebSocket client installed, which is exactly
    where an operator most wants to validate their configuration.
    """
    try:
        from oipulse.marketdata.runtime import build_spec_from_env, run_ingestor
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        print(
            f"cannot start the ingestor role: {exc.name} is not installed.\n"
            f"Install the runtime dependencies with:  pip install -e .",
            file=sys.stderr,
        )
        return 3

    try:
        spec = build_spec_from_env()
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return run_ingestor(settings, spec)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    # Configuration first: an invalid environment must stop the process here, before a
    # server socket, a database pool or a provider connection exists.
    try:
        settings = load_settings()
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    role = args.role if args.role != "all" else settings.role
    configure_logging(level=settings.log_level, role=role, instance_id=settings.instance_id)
    log = get_logger(__name__)

    for warning in settings.warnings:
        log.warning("configuration_warning", extra={"detail": warning})

    if args.check:
        log.info(
            "configuration_valid",
            extra={
                "role": role,
                "app_env": settings.app_env,
                "clock": type(SystemClock()).__name__,
                "live_trading_env_gates_open": settings.live_trading_env_gates_open,
            },
        )
        return 0

    if role == "api":
        return run_api(settings, args.host, args.port, args.reload)

    if role == "ingestor":
        return run_ingestor_role(settings)

    phase = _PHASE_OF_ROLE.get(role)
    print(
        f"role {role!r} has no runtime yet: implemented in {phase}.\n"
        f"Phase 1 supports --role api and --check for every role.",
        file=sys.stderr,
    )
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
