"""Unified parsing pipeline: fetch → ingest → score prices → analyze."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from app.parsers.avito import AvitoParser
from app.parsers.base import BaseParser, ParseResult
from app.parsers.ingestion import ingest_listings

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Aggregated result of a full pipeline run."""

    source_results: list[ParseResult] = field(default_factory=list)
    total_new: int = 0
    total_scored: int = 0
    total_analyzed: int = 0
    errors: list[str] = field(default_factory=list)


def get_all_parsers() -> list[BaseParser]:
    """Return all configured parsers."""
    parsers: list[BaseParser] = [
        AvitoParser(config_path="avipars/config.toml"),
    ]

    try:
        from app.parsers.drom import DromParser

        parsers.append(DromParser())
    except ImportError:
        pass

    try:
        from app.parsers.autoru import AutoruParser

        parsers.append(AutoruParser())
    except ImportError:
        pass

    return parsers


async def run_pipeline(parsers: list[BaseParser] | None = None) -> PipelineResult:
    """Execute the full pipeline:

    1. Fetch listings from all sources
    2. Ingest into PostgreSQL (dedup by source+external_id)
    3. Score prices with CatBoost model (P10/P50/P90)
    4. AI categorization of new listings
    """
    result = PipelineResult()

    if parsers is None:
        parsers = get_all_parsers()

    # Step 1+2: Fetch and ingest from ALL sources IN PARALLEL
    from app.parsers.proxy_manager import change_ip, check_proxy

    # Health-check proxy before starting
    check_proxy()

    async def _fetch_and_ingest(parser: BaseParser) -> ParseResult:
        source = parser.source_name
        logger.info("Pipeline: fetching from %s", source)
        start = time.time()
        try:
            listings = await parser.fetch_listings()
            elapsed = time.time() - start
            logger.info("Pipeline: %s returned %d listings in %.1fs", source, len(listings), elapsed)
            pr = await ingest_listings(listings, source)
            pr.elapsed_seconds = elapsed
            # Extract captcha count from parser if available
            if hasattr(parser, "_last_captcha_count"):
                pr.captchas_hit = parser._last_captcha_count
            return pr
        except Exception as exc:
            logger.exception("Pipeline: %s failed, changing IP and skipping", source)
            change_ip()
            result.errors.append(f"{source}: {exc}")
            return ParseResult(source=source, errors=1, elapsed_seconds=time.time() - start)

    parse_results = await asyncio.gather(*[_fetch_and_ingest(p) for p in parsers], return_exceptions=True)
    for i, pr in enumerate(parse_results):
        if isinstance(pr, BaseException):
            source = parsers[i].source_name
            logger.exception("Pipeline: %s raised unhandled exception", source, exc_info=pr)
            result.errors.append(f"{source}: {pr}")
            result.source_results.append(ParseResult(source=source, errors=1))
        else:
            result.source_results.append(pr)
            result.total_new += pr.new_saved

    # Step 2.5: Deduplicate across sources
    if result.total_new > 0:
        try:
            from app.db.session import async_session_factory
            from app.services.deduplication import detect_and_mark_duplicates

            async with async_session_factory() as session:
                dupes = await detect_and_mark_duplicates(session)
                if dupes:
                    logger.info("Pipeline: marked %d duplicates", dupes)
        except Exception as exc:
            logger.exception("Pipeline: deduplication failed")
            result.errors.append(f"dedup: {exc}")

    # Step 3: Score with price model
    if result.total_new > 0:
        try:
            from app.services.pricing_trainer import score_listings

            scored = await score_listings(limit=result.total_new + 50)
            result.total_scored = scored
            logger.info("Pipeline: scored %d listings", scored)
        except Exception as exc:
            logger.exception("Pipeline: price scoring failed")
            result.errors.append(f"pricing: {exc}")

    # Step 4: AI categorization (auto-scaling worker pool)
    if result.total_new > 0:
        try:
            from app.services.analysis_pool import run_analysis_pool

            pool_result = await run_analysis_pool(max_total=result.total_new + 50)
            result.total_analyzed = pool_result["total_processed"]
            logger.info(
                "Pipeline: analyzed %d listings via %d workers",
                pool_result["total_processed"],
                pool_result["workers"],
            )
        except Exception as exc:
            logger.exception("Pipeline: analysis failed")
            result.errors.append(f"analysis: {exc}")

    # Log per-source metrics
    for pr in result.source_results:
        lpb = pr.listings_per_ban
        lpb_str = f"{lpb:.0f}" if lpb is not None else "inf"
        logger.info(
            "Pipeline [%s]: fetched=%d, new=%d, dupes=%d, time=%.1fs, captchas=%d, listings/ban=%s",
            pr.source,
            pr.total_fetched,
            pr.new_saved,
            pr.duplicates_skipped,
            pr.elapsed_seconds,
            pr.captchas_hit,
            lpb_str,
        )

    logger.info(
        "Pipeline complete: new=%d, scored=%d, analyzed=%d, errors=%d",
        result.total_new,
        result.total_scored,
        result.total_analyzed,
        len(result.errors),
    )
    return result
