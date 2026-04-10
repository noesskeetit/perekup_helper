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

    Factors:
    - Price vs market: +2 pts per % below market (capped at 50% to avoid model errors)
    - AI category: clean +10, damaged/bad_docs/debtor → hard cap
    - Mileage per year: low km bonus, high km penalty
    - Photos: more photos = transparent seller
    - Freshness: newer listings are more actionable
    - Owners count: fewer owners = better
    - Data completeness: penalize missing critical fields
    - Minimum price sanity: very cheap listings are suspicious
    """
    score = 50  # neutral baseline

    # --- Additive factors (bonuses and penalties) ---

    # Price vs market: +1.5 pts per % below, capped at 30% diff
    # Above 30% is likely a model error for rare/old cars, not a real deal
    if listing.price_diff_pct:
        diff = float(listing.price_diff_pct)
        capped_diff = max(-30, min(30, diff))
        # Dampen for old cars — model over-predicts due to condition variance
        car_age = CURRENT_YEAR - (listing.year or CURRENT_YEAR)
        if car_age > 15:
            capped_diff *= 0.5  # halve the bonus for very old cars
        elif car_age > 10:
            capped_diff *= 0.75  # reduce for older cars
        score += capped_diff * 1.5

    # AI category bonus (only additive ones here; hard caps applied below)
    if listing.analysis:
        category = listing.analysis.category
        if category == "clean":
            score += 10
        elif category == "complex_but_profitable":
            score += 5

    # Low mileage bonus / high mileage penalty
    if listing.mileage and listing.year:
        car_age = max(1, CURRENT_YEAR - listing.year)
        mileage_per_year = listing.mileage / car_age
        if mileage_per_year < 10000:
            score += 5
        elif mileage_per_year > 30000:
            score -= 5
        elif mileage_per_year > 50000:
            score -= 10

    # Has photos bonus (transparent seller) — check higher threshold first
    if listing.photo_count and listing.photo_count > 10:
        score += 5
    elif listing.photo_count and listing.photo_count > 5:
        score += 3

    # Data completeness penalty — missing critical fields reduce confidence
    missing_fields = 0
    if not listing.mileage:
        missing_fields += 1
    if not listing.year:
        missing_fields += 1
    if not getattr(listing, "body_type", None) or listing.body_type == "unknown":
        missing_fields += 1
    if missing_fields >= 2:
        score -= 8
    elif missing_fields == 1:
        score -= 3

    # Freshness bonus
    if listing.created_at:
        from datetime import UTC, datetime

        age_hours = (datetime.now(UTC) - listing.created_at).total_seconds() / 3600
        if age_hours < 6:
            score += 8
        elif age_hours < 24:
            score += 4
        elif age_hours > 72:
            score -= 3

    # Owners count bonus/penalty
    if listing.owners_count:
        if listing.owners_count == 1:
            score += 5
        elif listing.owners_count >= 4:
            score -= 5

    # Mild penalty for very cheap cars (not a hard cap)
    price = getattr(listing, "price", 0) or 0
    if 0 < price < 200_000:
        score -= 5

    # --- Hard caps (applied LAST, before final clamp) ---
    # These override the score regardless of bonuses above.

    # Incomplete data cap — can't trust high scores without mileage + body info
    if missing_fields >= 2:
        score = min(score, 65)
    elif missing_fields == 1:
        score = min(score, 80)

    # Suspiciously cheap — likely garbage data or scam
    if 0 < price < 100_000:
        score = min(score, 20)

    # AI category hard caps for problem listings
    if listing.analysis:
        category = listing.analysis.category
        if category == "damaged_body":
            score = min(score, 15)
        elif category in ("bad_docs", "debtor"):
            score = min(score, 10)

    # High mileage hard cap — 300K+ km cars are worn out, not deals
    if listing.mileage and listing.mileage >= 300_000:
        score = min(score, 30)
    elif listing.mileage and listing.mileage >= 200_000:
        score = min(score, 55)

    # Mileage vs age sanity — if mileage is impossibly high for the age, data is suspect
    if listing.mileage and listing.year:
        car_age = max(1, CURRENT_YEAR - listing.year)
        if listing.mileage / car_age > 50_000:
            score = min(score, 40)

    # Keyword red flags from description AND URL
    desc = (listing.description or "").lower()
    url_lower = (getattr(listing, "url", None) or "").lower()
    text_to_check = f"{desc} {url_lower}"

    # Simple contains flags (safe — no common negation forms)
    red_flags = [
        "на запчасти",
        "под разбор",
        "не на ходу",
        "в залоге",
        "запрет",
        "не заводится",
        "утеряны документы",
        "без птс",
        "конструктор",
        "после дтп",
        "требует ремонт",
        "требует вложен",
        "bityy",  # Avito URL marker
    ]
    has_red_flag = any(flag in text_to_check for flag in red_flags)

    # "битый/битая" needs negation check — "не битый" means NOT damaged
    if not has_red_flag:
        for word in ("битый", "битая", "бит."):
            if word in text_to_check:
                # Check if preceded by "не " within 3 chars
                idx = text_to_check.find(word)
                prefix = text_to_check[max(0, idx - 4) : idx]
                if "не " not in prefix and "не-" not in prefix:
                    has_red_flag = True
                    break

    if has_red_flag:
        score = min(score, 15)

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
                Listing.deal_score.is_(None),
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

            # Store in deal_score column (always available)
            listing.deal_score = float(deal_score)

            # Also store in analysis.score if analysis exists
            if listing.analysis:
                listing.analysis.score = float(deal_score)

            scored += 1

        try:
            await session.commit()
            logger.info("Scored %d listings with deal scores", scored)
        except Exception:
            await session.rollback()
            logger.exception("Failed to save deal scores")
            return 0

        return scored
