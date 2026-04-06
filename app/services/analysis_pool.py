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
import time

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
        "batch_size": 5,  # Process 5 at a time with semaphore
        "concurrency": 3,  # Max concurrent Cloud.ru requests
    },
]

# Scaling thresholds: (min_backlog, num_workers)
SCALE_THRESHOLDS = [
    (500, 3),
    (50, 2),
    (0, 1),
]

# Maximum number of errors to keep in metrics per worker
MAX_ERRORS_PER_WORKER = 10


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


def _make_worker_metrics(
    worker_name: str,
    processed: int = 0,
    failed: int = 0,
    errors: list[str] | None = None,
    duration_seconds: float = 0.0,
) -> dict:
    """Create a standardized worker metrics dict."""
    return {
        "worker_name": worker_name,
        "processed": processed,
        "failed": failed,
        "errors": (errors or [])[:MAX_ERRORS_PER_WORKER],
        "duration_seconds": round(duration_seconds, 2),
    }


async def _run_worker(
    name: str,
    provider: str,
    model: str,
    batch_size: int,
    limit: int,
    concurrency: int = 1,
) -> dict:
    """Run a single analysis worker. Returns metrics dict."""
    logger.info(
        "Worker [%s] starting (provider=%s, model=%s, limit=%d)",
        name,
        provider,
        model,
        limit,
    )

    t0 = time.monotonic()
    try:
        if provider == "cloudru":
            processed = await _run_cloudru_worker(name, model, limit, concurrency)
        else:
            processed = await _run_openrouter_worker(name, model, batch_size, limit)

        duration = time.monotonic() - t0
        return _make_worker_metrics(
            worker_name=name,
            processed=processed,
            duration_seconds=duration,
        )
    except Exception as exc:
        duration = time.monotonic() - t0
        logger.error("Worker [%s] failed: %s", name, exc)
        return _make_worker_metrics(
            worker_name=name,
            failed=1,
            errors=[str(exc)],
            duration_seconds=duration,
        )


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


async def _run_cloudru_worker(name: str, model: str, limit: int, concurrency: int = 3) -> int:
    """Worker using Cloud.ru FM API with concurrent requests via semaphore.

    Instead of processing listings one at a time (1 req/s), we use an
    asyncio.Semaphore to allow up to ``concurrency`` requests in flight
    simultaneously, improving throughput by ~3x.
    """
    original_provider = os.environ.get("AI_PROVIDER", "")
    os.environ["AI_PROVIDER"] = "cloudru"

    try:
        from app.parsers.analyzer import analyze_new_listings

        if concurrency <= 1:
            # Fallback to sequential processing
            analyzed = await analyze_new_listings(limit=limit)
            logger.info("Worker [%s] done: analyzed %d", name, analyzed)
            return analyzed

        # Split work into chunks and run concurrently with semaphore
        sem = asyncio.Semaphore(concurrency)
        chunk_size = max(1, limit // concurrency)
        chunks = []
        remaining = limit
        while remaining > 0:
            size = min(chunk_size, remaining)
            chunks.append(size)
            remaining -= size

        async def _run_chunk(size: int) -> int:
            async with sem:
                return await analyze_new_listings(limit=size)

        results = await asyncio.gather(
            *[_run_chunk(c) for c in chunks],
            return_exceptions=True,
        )

        total = 0
        for r in results:
            if isinstance(r, Exception):
                logger.warning("Cloud.ru chunk failed: %s", r)
            else:
                total += r

        logger.info("Worker [%s] done: analyzed %d (concurrency=%d)", name, total, concurrency)
        return total
    finally:
        os.environ["AI_PROVIDER"] = original_provider


async def run_analysis_pool(max_total: int = 2000) -> dict:
    """Run auto-scaling analysis pool.

    Returns dict with per-worker metrics, aggregate totals, and backlog info.
    """
    backlog_before = await get_backlog_size()
    if backlog_before == 0:
        logger.info("Analysis pool: no backlog, skipping")
        return {
            "backlog_before": 0,
            "backlog_after": 0,
            "workers": 0,
            "total_processed": 0,
            "total_failed": 0,
            "total_errors": [],
            "worker_metrics": [],
        }

    num_workers = _pick_worker_count(backlog_before)
    per_worker = min(max_total // num_workers, backlog_before // num_workers + 1)
    configs = WORKER_CONFIGS[:num_workers]

    logger.info(
        "Analysis pool: backlog=%d, scaling to %d workers, %d per worker",
        backlog_before,
        num_workers,
        per_worker,
    )

    # Run workers concurrently
    tasks = []
    for cfg in configs:
        tasks.append(
            _run_worker(
                cfg["name"],
                cfg["provider"],
                cfg["model"],
                cfg["batch_size"],
                per_worker,
                cfg.get("concurrency", 1),
            )
        )

    worker_metrics = await asyncio.gather(*tasks, return_exceptions=True)

    # Normalize: if gather itself raised, wrap as error metrics
    normalized_metrics: list[dict] = []
    for i, m in enumerate(worker_metrics):
        if isinstance(m, Exception):
            cfg = configs[i]
            normalized_metrics.append(
                _make_worker_metrics(
                    worker_name=cfg["name"],
                    failed=1,
                    errors=[str(m)],
                )
            )
        else:
            normalized_metrics.append(m)

    # Aggregate
    total_processed = sum(m["processed"] for m in normalized_metrics)
    total_failed = sum(m["failed"] for m in normalized_metrics)
    all_errors: list[str] = []
    for m in normalized_metrics:
        all_errors.extend(m["errors"])

    backlog_after = await get_backlog_size()

    logger.info(
        "Analysis pool done: %d processed, %d failed by %d workers",
        total_processed,
        total_failed,
        num_workers,
    )

    return {
        "backlog_before": backlog_before,
        "backlog_after": backlog_after,
        "workers": num_workers,
        "total_processed": total_processed,
        "total_failed": total_failed,
        "total_errors": all_errors[:MAX_ERRORS_PER_WORKER],
        "worker_metrics": normalized_metrics,
    }
