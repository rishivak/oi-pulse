"""Worker process entry point.

Run with:  python run_worker.py

The worker process is separate from the API server but shares the same
codebase, database, and Redis instance.  Keep only one active worker instance
per DB unless you implement fine-grained distributed locking per job.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys

import structlog

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ]
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


async def main() -> None:
    from worker.scheduler import _run_catch_up, build_scheduler

    scheduler = build_scheduler()

    # Graceful shutdown
    loop = asyncio.get_running_loop()

    def _shutdown(sig: int):
        logger.info("Received signal %s — shutting down worker", sig)
        scheduler.shutdown(wait=False)
        loop.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown, sig)

    logger.info("Starting OI Pulse worker…")
    scheduler.start()

    # Run catch-up pass for missed buckets since last shutdown
    await _run_catch_up()

    # Keep running until signal
    try:
        await asyncio.Event().wait()
    except (asyncio.CancelledError, RuntimeError):
        pass


if __name__ == "__main__":
    asyncio.run(main())
