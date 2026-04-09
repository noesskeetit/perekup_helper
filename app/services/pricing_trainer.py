"""Training and scoring pipeline for the price model.

- train_model(): fetch all listings from DB, train CatBoost, save model
- score_listings(): apply model to unscored listings, update market_price + price_diff_pct

Outlier removal (v2):
  - IQR bounds tightened to 1.5x on both sides (was asymmetric 1.5/3.0).
  - Secondary MAD filter on log(price) per segment removes mis-priced listings
    that survive IQR (e.g., a 2M car listed at 200K by mistake).

MLflow integration (v3):
  - Each training run is logged to MLflow (local file tracking in mlruns/).
  - New models are saved to a temp directory first, then promoted to
    production only if P50 MAPE improves (or no prior model exists).
  - Rejected models are tagged in MLflow for auditability.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import select

from app.db.session import async_session_factory
from app.models.listing import Listing
from app.services.mlflow_tracking import log_training_run, promote_if_better
from app.services.pricing import CURRENT_YEAR, MODEL_DIR, get_price_model

logger = logging.getLogger(__name__)


async def train_model(*, exclude_sources: list[str] | None = None) -> dict:
    """Fetch all listings from DB and retrain the price model.

    Args:
        exclude_sources: Source names to exclude from training (e.g., ["autoru"]).

    Returns training stats dict.
    """
    async with async_session_factory() as session:
        from sqlalchemy.orm import selectinload

        stmt = (
            select(Listing)
            .options(selectinload(Listing.analysis))
            .where(
                Listing.price > 0,
                Listing.is_duplicate.is_(False),
            )
        )
        if exclude_sources:
            for src in exclude_sources:
                stmt = stmt.where(Listing.source != src)
        result = await session.execute(stmt)
        all_listings = list(result.scalars().unique().all())

    # Filter out problem listings that distort the price model.
    # "Cheap because damaged" ≠ "cheap because underpriced"
    problem_categories = {"damaged_body", "bad_docs", "debtor"}
    red_flag_keywords = [
        "на запчасти",
        "под разбор",
        "не на ходу",
        "в залоге",
        "не заводится",
        "утеряны документы",
        "без птс",
        "конструктор",
    ]

    listings = []
    filtered_count = 0
    for row in all_listings:
        # Filter by AI category
        if row.analysis and row.analysis.category in problem_categories:
            filtered_count += 1
            continue
        # Filter by description red flags
        desc = (row.description or "").lower()
        if any(flag in desc for flag in red_flag_keywords):
            filtered_count += 1
            continue
        listings.append(row)

    logger.info("Filtered %d problem listings from training (%d remaining)", filtered_count, len(listings))

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
                "mileage": row.mileage,
                "price": row.price,
                "source": row.source or "unknown",
                "city": row.city or "unknown",
                "engine_type": row.engine_type or "unknown",
                "transmission": row.transmission or "unknown",
                "drive_type": row.drive_type or "unknown",
                "body_type": row.body_type or "unknown",
                "engine_volume": row.engine_volume,
                "power_hp": row.power_hp,
                "owners_count": row.owners_count,
                "photo_count": row.photo_count or 0,
                "is_dealer": int(row.is_dealer) if row.is_dealer else 0,
                "listing_date": row.listing_date,
                "created_at": row.created_at,
                "description": getattr(row, "description", "") or "",
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

    # 4. Mileage vs age sanity filter — remove listings with impossible mileage
    mileage_removed = 0
    if "mileage" in df.columns and "year" in df.columns:
        car_age = CURRENT_YEAR - df["year"]
        # > 60K km/year is unrealistic for most passenger cars
        mileage_per_year = df["mileage"] / car_age.clip(lower=1)
        bad_mileage = mileage_per_year > 60_000
        mileage_removed = int(bad_mileage.sum())
        df = df[~bad_mileage].copy()

    # 5. Cross-source price sanity filter.
    #    Auto.ru sometimes captures monthly payments or down payments as prices.
    #    Remove listings whose price is <30% of the (brand, model) median across all sources.
    cross_source_removed = 0
    if len(df) > 100:
        median_prices = df.groupby(["brand", "model"])["price"].transform("median")
        # Flag listings with price < 30% of segment median (likely payment amounts, not prices)
        suspicious = df["price"] < (median_prices * 0.3)
        cross_source_removed = int(suspicious.sum())
        df = df[~suspicious].copy()

    logger.info(
        "Removed %d stale, %d IQR outliers, %d MAD outliers, %d bad mileage, %d cross-source suspicious from %d total (kept %d)",
        stale_count,
        outlier_count,
        mad_outlier_count,
        mileage_removed,
        cross_source_removed,
        total,
        len(df),
    )

    if df.empty:
        logger.warning("No listings left after cleaning")
        return {"status": "skipped", "reason": "no_data_after_cleaning"}

    model = get_price_model()
    stats = model.train(df)

    if stats.get("status") == "trained":
        # Save to a temp directory first so we can compare before promoting
        tmp_dir = Path(tempfile.mkdtemp(prefix="perekup_model_"))
        try:
            model.save(tmp_dir / "price_model.pkl")

            # Log the run to MLflow
            log_training_run(stats, tmp_dir)

            # Promote only if MAPE improved (or no prior model exists)
            promoted = promote_if_better(stats, tmp_dir)

            if promoted:
                # Reload the model from production dir so singleton is current
                model.load()
                logger.info("New model promoted to production")
            else:
                # Restore previous model from production dir
                if (MODEL_DIR / "price_model.pkl").exists():
                    model.load()
                    logger.info("Kept previous production model (new model rejected)")
                else:
                    # No previous model -- save anyway as first baseline
                    MODEL_DIR.mkdir(parents=True, exist_ok=True)
                    for f in tmp_dir.iterdir():
                        shutil.copy2(str(f), str(MODEL_DIR / f.name))
                    model.load()
                    logger.info("No previous model, saved as first baseline")

            stats["mlflow_promoted"] = promoted
        finally:
            # Clean up temp directory
            shutil.rmtree(tmp_dir, ignore_errors=True)

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
            # Pass raw values — model's _fill_defaults handles NaN properly
            feature_dicts.append(
                {
                    "brand": listing.brand or "unknown",
                    "model": listing.model or "unknown",
                    "year": listing.year or 2020,
                    "mileage": listing.mileage,
                    "price": listing.price,
                    "source": listing.source or "unknown",
                    "city": listing.city or "unknown",
                    "engine_type": listing.engine_type or "unknown",
                    "transmission": listing.transmission or "unknown",
                    "drive_type": listing.drive_type or "unknown",
                    "body_type": listing.body_type or "unknown",
                    "engine_volume": listing.engine_volume,
                    "power_hp": listing.power_hp,
                    "owners_count": listing.owners_count,
                    "photo_count": listing.photo_count or 0,
                    "is_dealer": int(listing.is_dealer) if listing.is_dealer else 0,
                    "listing_date": listing.listing_date,
                    "created_at": listing.created_at,
                    "description": getattr(listing, "description", "") or "",
                }
            )

        # Predict
        predictions = model.predict(feature_dicts)

        # Update DB
        scored = 0
        skipped_sanity = 0
        skipped_low_conf = 0
        for listing, pred in zip(listings, predictions, strict=False):
            p50 = pred["p50"]
            pct = pred["price_vs_market_pct"]
            confidence = pred.get("confidence", "low")

            if p50 and p50 > 0:
                # Sanity check: skip wildly wrong predictions.
                actual = listing.price
                if actual > 0:
                    ratio = p50 / actual
                    if ratio > 2.5 or ratio < 0.4:
                        skipped_sanity += 1
                        continue

                # Tighter sanity bounds for low-confidence predictions
                if confidence == "low" and actual > 0:
                    ratio = p50 / actual
                    if ratio > 1.8 or ratio < 0.55:
                        skipped_low_conf += 1
                        continue

                listing.market_price = p50
                # Clamp to NUMERIC(5,2) range: -999.99 .. 999.99
                if pct is not None:
                    pct = max(-999.99, min(999.99, pct))
                listing.price_diff_pct = pct
                scored += 1

        if skipped_sanity:
            logger.info("Skipped %d listings with unreliable predictions (>2.5x deviation)", skipped_sanity)
        if skipped_low_conf:
            logger.info("Skipped %d low-confidence predictions (tight bounds)", skipped_low_conf)

        try:
            await session.commit()
            logger.info("Scored %d listings with market prices", scored)
        except Exception:
            await session.rollback()
            logger.exception("Failed to save price scores")
            return 0

        return scored
