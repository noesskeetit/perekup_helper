"""Post-ingestion AI analysis: categorize new listings and compute scores."""

from __future__ import annotations

import logging
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session_factory
from app.models.listing import AnalysisCategory, Listing, ListingAnalysis
from perekup_helper.batch import BatchProcessor
from perekup_helper.models import ListingDescription

logger = logging.getLogger(__name__)

# Map perekup_helper CarCategory values → DB AnalysisCategory
_CATEGORY_MAP = {
    "clean": AnalysisCategory.CLEAN,
    "damaged_body": AnalysisCategory.DAMAGED_BODY,
    "document_issues": AnalysisCategory.BAD_DOCS,
    "owner_debtor": AnalysisCategory.DEBTOR,
    "complex_profitable": AnalysisCategory.COMPLEX_BUT_PROFITABLE,
    # junk has no DB enum — map to damaged_body as closest
    "junk": AnalysisCategory.DAMAGED_BODY,
}


async def analyze_new_listings(limit: int = 50) -> int:
    """Find listings without analysis and run AI categorization.

    Returns the number of listings analyzed.
    """
    async with async_session_factory() as session:
        # Find listings that don't have an analysis yet
        stmt = (
            select(Listing)
            .outerjoin(ListingAnalysis)
            .where(ListingAnalysis.id.is_(None))
            .where(Listing.description.isnot(None))
            .where(Listing.description != "")
            .order_by(Listing.created_at.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        listings = list(result.scalars().all())

        if not listings:
            logger.debug("No unanalyzed listings found")
            return 0

        logger.info("Analyzing %d new listings", len(listings))

        # Convert to ListingDescription for the categorizer
        descriptions = []
        for listing in listings:
            text_parts = []
            if listing.raw_data and listing.raw_data.get("title"):
                text_parts.append(listing.raw_data["title"])
            if listing.description:
                text_parts.append(listing.description)

            text = "\n".join(text_parts) if text_parts else f"{listing.brand} {listing.model} {listing.year}"

            descriptions.append(
                ListingDescription(
                    id=str(listing.id),
                    text=text,
                    price=listing.price,
                    market_price=listing.market_price,
                )
            )

        # Run categorization — Cloud.ru FM (primary) or OpenRouter (fallback)
        from app.config import settings

        score_results = []
        try:
            if settings.cloudru_fm_api_key:
                from perekup_helper.cloudru_client import CloudRuCategorizer

                categorizer = CloudRuCategorizer(api_key=settings.cloudru_fm_api_key)
                for desc in descriptions:
                    try:
                        sr = categorizer.categorize_and_score(desc)
                        score_results.append(sr)
                    except Exception:
                        logger.warning("Cloud.ru categorization failed for %s", desc.id, exc_info=True)
                    import time
                    time.sleep(1)  # rate limit safety
            else:
                api_key = settings.openrouter_api_key or os.environ.get("OPENROUTER_API_KEY", "")
                model = settings.openrouter_model or "qwen/qwen3.6-plus:free"
                processor = BatchProcessor(api_key=api_key, model=model)
                score_results = processor.process(descriptions)
        except Exception:
            logger.exception("AI categorization failed")
            return 0

        # Save results
        listing_map = {str(l.id): l for l in listings}
        analyzed_count = 0

        for sr in score_results:
            listing = listing_map.get(sr.listing_id)
            if not listing:
                continue

            cat_value = sr.category_result.category.value
            db_category = _CATEGORY_MAP.get(cat_value, AnalysisCategory.CLEAN)

            analysis = ListingAnalysis(
                listing_id=listing.id,
                category=db_category.value,
                confidence=sr.category_result.confidence,
                ai_summary=sr.category_result.reasoning,
                flags=sr.category_result.flags,
                score=sr.attractiveness_score,
            )
            session.add(analysis)

            # Update price_diff_pct if we computed it
            if sr.price_ratio is not None:
                listing.price_diff_pct = round((1.0 - sr.price_ratio) * 100, 2)

            analyzed_count += 1

        try:
            await session.commit()
            logger.info("Saved %d listing analyses", analyzed_count)
        except Exception:
            await session.rollback()
            # Retry one-by-one on conflict
            saved = 0
            for sr in score_results:
                listing = listing_map.get(sr.listing_id)
                if not listing:
                    continue
                cat_value = sr.category_result.category.value
                db_category = _CATEGORY_MAP.get(cat_value, AnalysisCategory.CLEAN)
                try:
                    async with async_session_factory() as retry_session:
                        # Check if already exists
                        existing = (await retry_session.execute(
                            select(ListingAnalysis).where(ListingAnalysis.listing_id == listing.id)
                        )).scalar_one_or_none()
                        if existing:
                            continue
                        analysis = ListingAnalysis(
                            listing_id=listing.id,
                            category=db_category.value,
                            confidence=sr.category_result.confidence,
                            ai_summary=sr.category_result.reasoning,
                            flags=sr.category_result.flags,
                            score=sr.attractiveness_score,
                        )
                        retry_session.add(analysis)
                        await retry_session.commit()
                        saved += 1
                except Exception:
                    pass
            analyzed_count = saved
            logger.info("Saved %d analyses (retry mode)", saved)

        return analyzed_count
