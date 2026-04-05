"""Periodic scheduler: parsing every N minutes, model retraining daily."""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def _parse_job() -> None:
    """Run full parse pipeline for all sources."""
    from app.parsers.pipeline import run_pipeline

    logger.info("Scheduler: starting periodic parse (all sources)")
    try:
        result = await run_pipeline()
        logger.info(
            "Scheduler: parse complete — new=%d, scored=%d, analyzed=%d, errors=%d",
            result.total_new,
            result.total_scored,
            result.total_analyzed,
            len(result.errors),
        )
        for err in result.errors:
            logger.warning("Scheduler: pipeline error — %s", err)
    except Exception:
        logger.exception("Scheduler: parse job failed")


async def _retrain_price_model_job() -> None:
    """Retrain CatBoost price model on all collected data."""
    from app.services.pricing_trainer import train_model, score_listings

    logger.info("Scheduler: retraining price model")
    try:
        stats = await train_model()
        logger.info("Scheduler: model training result — %s", stats)

        if stats.get("status") == "trained":
            scored = await score_listings(limit=2000)
            logger.info("Scheduler: re-scored %d listings after retraining", scored)
    except Exception:
        logger.exception("Scheduler: price model retraining failed")


def start_scheduler() -> AsyncIOScheduler:
    """Start the async periodic scheduler; idempotent if already running."""
    global _scheduler

    if _scheduler is not None and _scheduler.running:
        logger.warning("Scheduler already running, skipping start")
        return _scheduler

    _scheduler = AsyncIOScheduler()

    # Parse every N minutes
    _scheduler.add_job(
        _parse_job,
        "interval",
        minutes=settings.parse_interval_minutes,
        id="parse_all_sources",
        name="Parse all sources",
        max_instances=1,
    )

    # Retrain price model daily at 4 AM
    _scheduler.add_job(
        _retrain_price_model_job,
        "cron",
        hour=4,
        minute=0,
        id="retrain_price_model",
        name="Retrain price model",
        max_instances=1,
    )

    _scheduler.start()
    logger.info("Scheduler started: parse every %d min, model retrain daily at 04:00", settings.parse_interval_minutes)
    return _scheduler


def stop_scheduler() -> None:
    """Gracefully stop the scheduler."""
    global _scheduler

    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler stopped")
