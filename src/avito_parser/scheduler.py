"""Periodic task scheduler for auto-updating ads."""

import asyncio
import dataclasses
import logging

from apscheduler.schedulers.background import BackgroundScheduler

from .config import settings
from .listing_parser import SearchFilters
from .pipeline import scrape_and_save

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _run_scrape_job(filters_dict: dict):
    """Synchronous wrapper for the async scrape_and_save."""
    filters = SearchFilters(**filters_dict)
    loop = asyncio.new_event_loop()
    try:
        count = loop.run_until_complete(scrape_and_save(filters))
        logger.info("Periodic scrape complete: %d ads saved/updated", count)
    except Exception as e:
        logger.error("Periodic scrape failed: %s", e)
    finally:
        loop.close()


def start_scheduler(filters: SearchFilters) -> BackgroundScheduler:
    """Start periodic scraping with configured interval."""
    global _scheduler

    if _scheduler and _scheduler.running:
        logger.warning("Scheduler already running")
        return _scheduler

    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        _run_scrape_job,
        "interval",
        minutes=settings.update_interval_minutes,
        args=[dataclasses.asdict(filters)],
        id="avito_scrape",
        name="Avito auto scraper",
        max_instances=1,
    )
    _scheduler.start()
    logger.info("Scheduler started, interval: %d minutes", settings.update_interval_minutes)
    return _scheduler


def stop_scheduler():
    """Stop the periodic scheduler."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler stopped")
