"""Auto-scaling AI analysis worker pool.

Scales workers based on backlog size:
- <50 unanalyzed:   1 worker  (openrouter/mistral-nemo)
- 50-500:           2 workers (+openrouter/qwen-2.5-7b)
- 500+:             3 workers (+cloud.ru GLM-4.7)

Each worker pulls batches from DB independently. Workers don't overlap
because each claims listings by writing analysis rows atomically.
"""

from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy import func, select

from app.db.session import async_session_factory
from app.models.listing import Listing, ListingAnalysis

logger = logging.getLogger(__name__)

# Worker definitions: (name, provider, model, batch_size)
WORKER_CONFIGS = [
    {
        "name": "mistral-nemo",
        "provider": "openrouter",
        "model": "mistralai/mistral-nemo",
        "batch_size": 10,
    },
    {
        "name": "qwen-2.5-7b",
        "provider": "openrouter",
        "model": "qwen/qwen-2.5-7b-instruct",
        "batch_size": 10,
    },
    {
        "name": "cloudru-glm",
        "provider": "cloudru",
        "model": "zai-org/GLM-4.7",
        "batch_size": 1,  # Cloud.ru does 1 at a time
    },
]

# Scaling thresholds: (min_backlog, num_workers)
SCALE_THRESHOLDS = [
    (500, 3),
    (50, 2),
    (0, 1),
]


async def get_backlog_size() -> int:
    """Count listings that need analysis."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(func.count())
            .select_from(Listing)
            .outerjoin(ListingAnalysis)
            .where(
                ListingAnalysis.id.is_(None),
                Listing.is_duplicate.is_(False),
                Listing.description.isnot(None),
                Listing.description != "",
            )
        )
        return result.scalar() or 0


def _pick_worker_count(backlog: int) -> int:
    """Determine how many workers to run based on backlog size."""
    for threshold, count in SCALE_THRESHOLDS:
        if backlog >= threshold:
            return count
    return 1


async def _run_worker(name: str, provider: str, model: str, batch_size: int, limit: int) -> int:
    """Run a single analysis worker. Returns number analyzed."""
    logger.info("Worker [%s] starting (provider=%s, model=%s, limit=%d)", name, provider, model, limit)

    if provider == "cloudru":
        return await _run_cloudru_worker(name, model, limit)
    else:
        return await _run_openrouter_worker(name, model, batch_size, limit)


async def _run_openrouter_worker(name: str, model: str, batch_size: int, limit: int) -> int:
    """Worker using OpenRouter BatchProcessor."""
    from app.parsers.analyzer import analyze_new_listings

    # Temporarily override the model for this worker
    original_model = os.environ.get("OPENROUTER_MODEL", "")
    os.environ["OPENROUTER_MODEL"] = model
    os.environ["AI_PROVIDER"] = "openrouter"

    try:
        analyzed = await analyze_new_listings(limit=limit)
        logger.info("Worker [%s] done: analyzed %d", name, analyzed)
        return analyzed
    finally:
        os.environ["OPENROUTER_MODEL"] = original_model


async def _run_cloudru_worker(name: str, model: str, limit: int) -> int:
    """Worker using Cloud.ru FM API."""
    original_provider = os.environ.get("AI_PROVIDER", "")
    os.environ["AI_PROVIDER"] = "cloudru"

    try:
        from app.parsers.analyzer import analyze_new_listings

        analyzed = await analyze_new_listings(limit=limit)
        logger.info("Worker [%s] done: analyzed %d", name, analyzed)
        return analyzed
    finally:
        os.environ["AI_PROVIDER"] = original_provider


async def run_analysis_pool(max_total: int = 2000) -> dict:
    """Run auto-scaling analysis pool.

    Returns dict with per-worker stats and total analyzed.
    """
    backlog = await get_backlog_size()
    if backlog == 0:
        logger.info("Analysis pool: no backlog, skipping")
        return {"backlog": 0, "workers": 0, "analyzed": 0}

    num_workers = _pick_worker_count(backlog)
    per_worker = min(max_total // num_workers, backlog // num_workers + 1)
    configs = WORKER_CONFIGS[:num_workers]

    logger.info(
        "Analysis pool: backlog=%d, scaling to %d workers, %d per worker",
        backlog,
        num_workers,
        per_worker,
    )

    # Run workers concurrently
    tasks = []
    for cfg in configs:
        tasks.append(_run_worker(cfg["name"], cfg["provider"], cfg["model"], cfg["batch_size"], per_worker))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    total_analyzed = 0
    worker_stats = []
    for cfg, result in zip(configs, results, strict=False):
        if isinstance(result, Exception):
            logger.error("Worker [%s] failed: %s", cfg["name"], result)
            worker_stats.append({"name": cfg["name"], "analyzed": 0, "error": str(result)})
        else:
            total_analyzed += result
            worker_stats.append({"name": cfg["name"], "analyzed": result})

    logger.info("Analysis pool done: %d total analyzed by %d workers", total_analyzed, num_workers)
    return {
        "backlog": backlog,
        "workers": num_workers,
        "analyzed": total_analyzed,
        "details": worker_stats,
    }
