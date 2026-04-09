"""Standalone scheduler runner for Docker.

Starts APScheduler with parse + retrain jobs and runs forever.
"""

import asyncio
import contextlib
import logging
import signal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

from app.scheduler import start_scheduler  # noqa: E402


def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    sched = start_scheduler()

    # Graceful shutdown on SIGTERM (Docker stop)
    with contextlib.suppress(NotImplementedError):
        loop.add_signal_handler(signal.SIGTERM, sched.shutdown)

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        sched.shutdown()


if __name__ == "__main__":
    main()
