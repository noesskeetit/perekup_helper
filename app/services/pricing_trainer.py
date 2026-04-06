"""Training and scoring pipeline for the price model.

- train_model(): fetch all listings from DB, train CatBoost, save model
- score_listings(): apply model to unscored listings, update market_price + price_diff_pct
"""

from __future__ import annotations

import logging

import pandas as pd
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session_factory
from app.models.listing import Listing
from app.services.pricing import PriceModel, get_price_model

logger = logging.getLogger(__name__)


async def train_model() -> dict:
    """Fetch all listings from DB and retrain the price model.

    Returns training stats dict.
    """
    async with async_session_factory() as session:
        stmt = select(Listing).where(
            Listing.price > 0,
            Listing.is_duplicate.is_(False),
        )
        result = await session.execute(stmt)
        listings = list(result.scalars().all())

    if not listings:
        logger.warning("No listings in DB for training")
        return {"status": "skipped", "reason": "no_data"}

    # Build DataFrame
    records = []
    for row in listings:
        records.append({
            "id": str(row.id),
            "brand": row.brand or "unknown",
            "model": row.model or "unknown",
            "year": row.year or 2020,
            "mileage": row.mileage or 0,
            "price": row.price,
            "source": row.source or "unknown",
            "city": row.city or "unknown",
            "engine_type": row.engine_type or "unknown",
            "transmission": row.transmission or "unknown",
            "drive_type": row.drive_type or "unknown",
            "body_type": row.body_type or "unknown",
            "engine_volume": row.engine_volume or 0.0,
            "power_hp": row.power_hp or 0,
            "owners_count": row.owners_count or 0,
        })

    df = pd.DataFrame(records)
    logger.info("Training price model on %d listings", len(df))

    model = get_price_model()
    stats = model.train(df)

    if stats.get("status") == "trained":
        model.save()

    return stats


async def score_listings(limit: int = 500) -> int:
    """Apply price model to listings without market_price.

    Updates market_price (P50) and price_diff_pct in DB.
    Returns number of listings scored.
    """
    model = get_price_model()
    if not model.is_trained:
        logger.info("Price model not trained yet, skipping scoring")
        return 0

    async with async_session_factory() as session:
        # Find listings without market_price (skip year=0 — can't predict accurately)
        stmt = (
            select(Listing)
            .where(Listing.market_price.is_(None))
            .where(Listing.price > 0)
            .where(Listing.year >= 1990)
            .order_by(Listing.created_at.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        listings = list(result.scalars().all())

        if not listings:
            return 0

        # Prepare features
        feature_dicts = []
        for listing in listings:
            feature_dicts.append({
                "brand": listing.brand or "unknown",
                "model": listing.model or "unknown",
                "year": listing.year or 2020,
                "mileage": listing.mileage or 0,
                "price": listing.price,
                "source": listing.source or "unknown",
                "city": listing.city or "unknown",
                "engine_type": listing.engine_type or "unknown",
                "transmission": listing.transmission or "unknown",
                "drive_type": listing.drive_type or "unknown",
                "body_type": listing.body_type or "unknown",
                "engine_volume": listing.engine_volume or 0.0,
                "power_hp": listing.power_hp or 0,
                "owners_count": listing.owners_count or 0,
            })

        # Predict
        predictions = model.predict(feature_dicts)

        # Update DB
        scored = 0
        for listing, pred in zip(listings, predictions):
            p50 = pred["p50"]
            pct = pred["price_vs_market_pct"]

            if p50 and p50 > 0:
                listing.market_price = p50
                listing.price_diff_pct = pct
                scored += 1

        try:
            await session.commit()
            logger.info("Scored %d listings with market prices", scored)
        except Exception:
            await session.rollback()
            logger.exception("Failed to save price scores")
            return 0

        return scored
