"""Training and scoring pipeline for the price model.

- train_model(): fetch all listings from DB, train CatBoost, save model
- score_listings(): apply model to unscored listings, update market_price + price_diff_pct

Outlier removal (v2):
  - IQR bounds tightened to 1.5x on both sides (was asymmetric 1.5/3.0).
  - Secondary MAD filter on log(price) per segment removes mis-priced listings
    that survive IQR (e.g., a 2M car listed at 200K by mistake).
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select

from app.db.session import async_session_factory
from app.models.listing import Listing
from app.services.pricing import CURRENT_YEAR, get_price_model

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
        records.append(
            {
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
                "photo_count": row.photo_count or 0,
                "is_dealer": int(row.is_dealer) if row.is_dealer else 0,
                "listing_date": row.listing_date,
                "created_at": row.created_at,
            }
        )

    df = pd.DataFrame(records)
    total = len(df)
    logger.info("Training price model on %d listings", total)

    # --- Data cleaning ---
    # 1. Remove stale listings (older than 60 days)
    cutoff = datetime.now(UTC) - timedelta(days=60)
    df["created_at"] = pd.to_datetime(df["created_at"], utc=True)
    fresh_mask = df["created_at"] >= cutoff
    stale_count = int((~fresh_mask).sum())
    df = df[fresh_mask].copy()

    # 2. IQR outlier removal per (brand, model) segment — symmetric 1.5x bounds
    outlier_count = 0
    clean_parts: list[pd.DataFrame] = []
    small_parts: list[pd.DataFrame] = []

    for _key, group in df.groupby(["brand", "model"]):
        if len(group) < 5:
            small_parts.append(group)
            continue
        q1 = group["price"].quantile(0.25)
        q3 = group["price"].quantile(0.75)
        iqr = q3 - q1
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        mask = (group["price"] >= lower) & (group["price"] <= upper)
        outlier_count += int((~mask).sum())
        clean_parts.append(group[mask])

    df = pd.concat(clean_parts + small_parts, ignore_index=True)

    # 3. MAD-based secondary filter on log(price) per (brand, model) segment.
    #    Catches mis-priced listings that survive IQR (e.g., typos, test listings).
    mad_outlier_count = 0
    mad_clean_parts: list[pd.DataFrame] = []

    for _key, group in df.groupby(["brand", "model"]):
        if len(group) < 10:
            mad_clean_parts.append(group)
            continue
        log_prices = np.log(group["price"].values.astype(float))
        median_log = float(np.median(log_prices))
        mad = float(np.median(np.abs(log_prices - median_log)))
        if mad < 1e-9:
            # All prices nearly identical — keep everything
            mad_clean_parts.append(group)
            continue
        # Modified Z-score: |log_price - median| / MAD > 3.5 is an outlier
        z_scores = np.abs(log_prices - median_log) / mad
        mask = z_scores <= 3.5
        mad_outlier_count += int((~mask).sum())
        mad_clean_parts.append(group[mask])

    df = pd.concat(mad_clean_parts, ignore_index=True)

    logger.info(
        "Removed %d stale, %d IQR outliers, %d MAD outliers from %d total (kept %d)",
        stale_count,
        outlier_count,
        mad_outlier_count,
        total,
        len(df),
    )

    if df.empty:
        logger.warning("No listings left after cleaning")
        return {"status": "skipped", "reason": "no_data_after_cleaning"}

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
            year = listing.year or 2020
            mileage = listing.mileage or 0
            car_age = CURRENT_YEAR - year

            feature_dicts.append(
                {
                    "brand": listing.brand or "unknown",
                    "model": listing.model or "unknown",
                    "year": year,
                    "mileage": mileage,
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
                    "photo_count": listing.photo_count or 0,
                    "is_dealer": int(listing.is_dealer) if listing.is_dealer else 0,
                    "listing_date": listing.listing_date,
                    "created_at": listing.created_at,
                    "car_age": car_age,
                    "log_car_age": math.log(car_age + 1),
                    "log_mileage": math.log(mileage + 1),
                    "mileage_per_year": mileage / max(car_age, 1),
                    "mileage_ratio": mileage / (car_age * 15_000 + 1),
                }
            )

        # Predict
        predictions = model.predict(feature_dicts)

        # Update DB
        scored = 0
        for listing, pred in zip(listings, predictions, strict=False):
            p50 = pred["p50"]
            pct = pred["price_vs_market_pct"]

            if p50 and p50 > 0:
                listing.market_price = p50
                # Clamp to NUMERIC(5,2) range: -999.99 .. 999.99
                if pct is not None:
                    pct = max(-999.99, min(999.99, pct))
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
