"""Periodic parse scheduler integrated with FastAPI lifespan."""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from avito_parser.listing_parser import SearchFilters
from avito_parser.pipeline import scrape_and_save

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def _parse_job() -> None:
    """Run full parse pipeline for all sources and log results."""
    logger.info("Scheduler: starting periodic parse (all sources)")
    filters = SearchFilters()
    try:
        result = await scrape_and_save(filters)
        logger.info(
            "Scheduler: parse complete — new=%d, updated=%d, analyzed=%d",
            result.new,
            result.updated,
            result.analyzed,
        )
    except Exception:
        logger.exception("Scheduler: parse job failed")


def start_scheduler() -> AsyncIOScheduler:
    """Start the async periodic scheduler; idempotent if already running."""
    global _scheduler

    if _scheduler is not None and _scheduler.running:
        logger.warning("Scheduler already running, skipping start")
        return _scheduler

    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        _parse_job,
        "interval",
        minutes=settings.parse_interval_minutes,
        id="parse_all_sources",
        name="Parse all sources",
        max_instances=1,
    )
    _scheduler.start()
    logger.info("Scheduler started, interval: %d min", settings.parse_interval_minutes)
    return _scheduler


def stop_scheduler() -> None:
    """Gracefully stop the scheduler."""
    global _scheduler

    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler stopped")
