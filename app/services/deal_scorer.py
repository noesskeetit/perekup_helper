"""Deal scoring service.

Computes a multi-factor "deal score" (0-100) for listings, replacing
the naive price_diff_pct > 15% check with a holistic evaluation.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.session import async_session_factory
from app.models.listing import Listing, ListingAnalysis

logger = logging.getLogger(__name__)

CURRENT_YEAR = 2026


async def compute_deal_score(listing: Listing) -> int:
    """Score 0-100 how good a deal this listing is.

    Factors considered:
    - Price vs market (biggest weight)
    - AI analysis category (clean/damaged/bad docs/debtor)
    - Mileage per year of age
    - Photo count (more photos = more transparent seller)
    """
    score = 50  # neutral baseline

    # Price vs market (biggest factor): +2 points per % below market, -2 per % above
    if listing.price_diff_pct:
        score += float(listing.price_diff_pct) * 2

    # AI category bonus/penalty
    if listing.analysis:
        category = listing.analysis.category
        if category == "clean":
            score += 10
        elif category == "damaged_body":
            score -= 20
        elif category == "bad_docs":
            score -= 30
        elif category == "debtor":
            score -= 25
        elif category == "complex_but_profitable":
            score += 5

    # Low mileage bonus / high mileage penalty
    if listing.mileage and listing.year:
        car_age = max(1, CURRENT_YEAR - listing.year)
        mileage_per_year = listing.mileage / car_age
        if mileage_per_year < 10000:
            score += 5  # low mileage
        elif mileage_per_year > 30000:
            score -= 5  # high mileage

    # Has photos bonus (transparent seller)
    if listing.photo_count and listing.photo_count > 5:
        score += 3

    # Clamp to 0-100
    return max(0, min(100, int(score)))


async def score_deals(limit: int = 500) -> int:
    """Score unscored listings and persist the deal score.

    Writes to ListingAnalysis.score when analysis exists,
    or stores in Listing.raw_data["deal_score"] as fallback.

    Returns number of listings scored.
    """
    async with async_session_factory() as session:
        # Fetch listings that have no deal score yet.
        # Prefer those with analysis, but also pick up unanalyzed ones.
        stmt = (
            select(Listing)
            .options(selectinload(Listing.analysis))
            .outerjoin(ListingAnalysis)
            .where(
                Listing.is_duplicate.is_(False),
                Listing.price > 0,
            )
            .order_by(Listing.created_at.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        listings = list(result.scalars().unique().all())

        if not listings:
            return 0

        scored = 0
        for listing in listings:
            deal_score = await compute_deal_score(listing)

            if listing.analysis:
                listing.analysis.score = float(deal_score)
            else:
                # No analysis row yet -- store in raw_data as fallback
                raw = dict(listing.raw_data) if listing.raw_data else {}
                raw["deal_score"] = deal_score
                listing.raw_data = raw

            scored += 1

        try:
            await session.commit()
            logger.info("Scored %d listings with deal scores", scored)
        except Exception:
            await session.rollback()
            logger.exception("Failed to save deal scores")
            return 0

        return scored
