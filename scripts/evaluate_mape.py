"""Evaluate CatBoost pricing model MAPE with cross-validation.

Loads data from DB, applies same cleaning as pricing_trainer,
runs 5-fold CV and reports MAPE by segment.
"""

from __future__ import annotations

import asyncio
import logging
import math
import sys
import time
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor, Pool
from sqlalchemy import select

# Add project root to path
sys.path.insert(0, ".")

from app.db.session import async_session_factory
from app.models.listing import Listing
from app.services.pricing import (
    ALL_FEATURES,
    CAT_FEATURES,
    NUM_FEATURES,
    PREMIUM_BRANDS,
    PriceModel,
    _CB_PARAMS_BASE,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


async def load_data() -> pd.DataFrame:
    """Load listings from DB as DataFrame."""
    async with async_session_factory() as session:
        stmt = select(Listing).where(
            Listing.price > 0,
            Listing.is_duplicate.is_(False),
        )
        result = await session.execute(stmt)
        listings = list(result.scalars().all())

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
                "color": row.color or "unknown",
                "steering_wheel": row.steering_wheel or "unknown",
                "pts_type": row.pts_type or "unknown",
                "seller_type": row.seller_type or "unknown",
                "condition": row.condition or "unknown",
                "generation": row.generation or "unknown",
                "region": row.region or "unknown",
            }
        )

    return pd.DataFrame(records)


def clean_data(df: pd.DataFrame) -> pd.DataFrame:
    """Apply same cleaning as pricing_trainer."""
    df = df.copy()

    # Price bounds
    df = df[(df["price"] >= 50_000) & (df["price"] <= 20_000_000)]

    # Year bounds
    df = df[(df["year"] >= 1990) & (df["year"] <= 2027)]

    # Remove stale (>60 days)
    cutoff = datetime.now(UTC) - timedelta(days=60)
    df["created_at"] = pd.to_datetime(df["created_at"], utc=True)
    df = df[df["created_at"] >= cutoff].copy()

    # IQR outlier removal per brand+model
    clean_parts = []
    small_parts = []
    iqr_removed = 0
    for _, group in df.groupby(["brand", "model"]):
        if len(group) < 5:
            small_parts.append(group)
            continue
        q1 = group["price"].quantile(0.25)
        q3 = group["price"].quantile(0.75)
        iqr = q3 - q1
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        mask = (group["price"] >= lower) & (group["price"] <= upper)
        iqr_removed += int((~mask).sum())
        clean_parts.append(group[mask])
    df = pd.concat(clean_parts + small_parts, ignore_index=True)

    # MAD filter
    mad_parts = []
    mad_removed = 0
    for _, group in df.groupby(["brand", "model"]):
        if len(group) < 10:
            mad_parts.append(group)
            continue
        log_prices = np.log(group["price"].values.astype(float))
        median_log = float(np.median(log_prices))
        mad = float(np.median(np.abs(log_prices - median_log)))
        if mad < 1e-9:
            mad_parts.append(group)
            continue
        z_scores = np.abs(log_prices - median_log) / mad
        mask = z_scores <= 3.5
        mad_removed += int((~mask).sum())
        mad_parts.append(group[mask])
    df = pd.concat(mad_parts, ignore_index=True)

    logger.info(f"After cleaning: {len(df)} rows (IQR removed {iqr_removed}, MAD removed {mad_removed})")

    # Shuffle
    df = df.sample(frac=1.0, random_state=42).reset_index(drop=True)
    return df


def cross_validate(
    df: pd.DataFrame,
    features: list[str],
    cat_features: list[str],
    params: dict,
    n_folds: int = 5,
    label: str = "baseline",
) -> dict:
    """Run k-fold CV and return detailed MAPE results."""
    model_helper = PriceModel()
    df = model_helper._fill_defaults(df)

    X = df[features]
    y = df["price"].values

    # Sample weights
    now = datetime.now(UTC)
    if "listing_date" in df.columns:
        ref_dates = pd.to_datetime(df["listing_date"], utc=True).fillna(now)
    elif "created_at" in df.columns:
        ref_dates = pd.to_datetime(df["created_at"], utc=True).fillna(now)
    else:
        ref_dates = pd.Series([now] * len(df))
    days_old = (now - ref_dates).dt.total_seconds() / 86400.0
    weights = np.exp(-0.007 * days_old.values)

    cat_indices = [features.index(f) for f in cat_features if f in features]

    fold_size = len(X) // n_folds
    all_preds = np.zeros(len(X))
    all_actuals = np.zeros(len(X))
    fold_mapes = []

    t0 = time.time()

    for fold in range(n_folds):
        val_start = fold * fold_size
        val_end = val_start + fold_size if fold < n_folds - 1 else len(X)

        X_train = pd.concat([X.iloc[:val_start], X.iloc[val_end:]])
        y_train = np.concatenate([y[:val_start], y[val_end:]])
        w_train = np.concatenate([weights[:val_start], weights[val_end:]])

        X_val = X.iloc[val_start:val_end]
        y_val = y[val_start:val_end]

        # Log target
        y_train_log = np.log(y_train)

        model = CatBoostRegressor(
            **params,
            loss_function="RMSE",
            eval_metric="MAPE",
            cat_features=cat_indices,
        )

        train_pool = Pool(X_train, y_train_log, cat_features=cat_indices, weight=w_train)
        val_pool = Pool(X_val, np.log(y_val), cat_features=cat_indices)
        model.fit(train_pool, eval_set=val_pool, verbose=0)

        y_pred = np.exp(model.predict(X_val))
        fold_mape = float(np.mean(np.abs((y_val - y_pred) / y_val)) * 100)
        fold_mapes.append(fold_mape)

        all_preds[val_start:val_end] = y_pred
        all_actuals[val_start:val_end] = y_val

    elapsed = time.time() - t0
    overall_mape = float(np.mean(np.abs((all_actuals - all_preds) / all_actuals)) * 100)
    cv_mape = float(np.mean(fold_mapes))

    # Per-brand MAPE
    brand_mapes = {}
    brands = df["brand"].values
    for brand in sorted(set(brands)):
        mask = brands == brand
        if mask.sum() < 20:
            continue
        b_actual = all_actuals[mask]
        b_pred = all_preds[mask]
        b_mape = float(np.mean(np.abs((b_actual - b_pred) / b_actual)) * 100)
        brand_mapes[brand] = {"mape": round(b_mape, 1), "count": int(mask.sum())}

    # Per price segment
    segment_mapes = {}
    for seg_name, lo, hi in [
        ("economy <800K", 0, 800_000),
        ("mid 800K-1.5M", 800_000, 1_500_000),
        ("premium >1.5M", 1_500_000, 1e9),
    ]:
        mask = (all_actuals >= lo) & (all_actuals < hi)
        if mask.sum() < 20:
            continue
        s_mape = float(np.mean(np.abs((all_actuals[mask] - all_preds[mask]) / all_actuals[mask])) * 100)
        segment_mapes[seg_name] = {"mape": round(s_mape, 1), "count": int(mask.sum())}

    result = {
        "label": label,
        "overall_mape": round(overall_mape, 2),
        "cv_mape": round(cv_mape, 2),
        "fold_mapes": [round(m, 1) for m in fold_mapes],
        "n_samples": len(df),
        "n_features": len(features),
        "elapsed_sec": round(elapsed, 1),
        "brand_mapes": brand_mapes,
        "segment_mapes": segment_mapes,
    }
    return result


def print_results(r: dict):
    """Pretty-print evaluation results."""
    print(f"\n{'=' * 60}")
    print(f"  {r['label']}")
    print(f"{'=' * 60}")
    print(f"  Overall MAPE: {r['overall_mape']:.2f}%")
    print(f"  CV MAPE:      {r['cv_mape']:.2f}%")
    print(f"  Fold MAPEs:   {r['fold_mapes']}")
    print(f"  Samples:      {r['n_samples']}")
    print(f"  Features:     {r['n_features']}")
    print(f"  Time:         {r['elapsed_sec']}s")

    print(f"\n  --- By price segment ---")
    for seg, info in sorted(r["segment_mapes"].items()):
        print(f"    {seg:20s}  MAPE={info['mape']:5.1f}%  n={info['count']}")

    print(f"\n  --- By brand (top 10 worst) ---")
    worst = sorted(r["brand_mapes"].items(), key=lambda x: -x[1]["mape"])[:10]
    for brand, info in worst:
        print(f"    {brand:20s}  MAPE={info['mape']:5.1f}%  n={info['count']}")

    print(f"\n  --- By brand (top 5 best) ---")
    best = sorted(r["brand_mapes"].items(), key=lambda x: x[1]["mape"])[:5]
    for brand, info in best:
        print(f"    {brand:20s}  MAPE={info['mape']:5.1f}%  n={info['count']}")


async def main():
    logger.info("Loading data from DB...")
    df_raw = await load_data()
    logger.info(f"Loaded {len(df_raw)} listings")

    df = clean_data(df_raw)

    # --- Baseline: current model ---
    baseline_params = {**_CB_PARAMS_BASE}
    baseline = cross_validate(
        df,
        features=ALL_FEATURES,
        cat_features=CAT_FEATURES,
        params=baseline_params,
        label="BASELINE (current model)",
    )
    print_results(baseline)

    # --- V3: more features + tuning ---
    # Add new categorical features
    v3_cat_features = CAT_FEATURES + ["color", "steering_wheel", "pts_type", "brand_model"]
    # brand_model combined feature
    df["brand_model"] = df["brand"].astype(str) + "_" + df["model"].astype(str)

    v3_num_features = NUM_FEATURES + ["log_engine_volume"]
    df["log_engine_volume"] = df["engine_volume"].astype(float).clip(lower=0.1).apply(lambda x: math.log(x))

    v3_features = v3_cat_features + v3_num_features

    v3_params = {
        "iterations": 2500,
        "learning_rate": 0.02,
        "depth": 8,
        "l2_leaf_reg": 5.0,
        "min_data_in_leaf": 10,
        "verbose": 0,
        "random_seed": 42,
        "early_stopping_rounds": 150,
    }

    v3 = cross_validate(
        df,
        features=v3_features,
        cat_features=v3_cat_features,
        params=v3_params,
        label="V3: +brand_model,color,steering,pts,log_engine_vol + tuning",
    )
    print_results(v3)

    # --- V3b: Huber loss instead of RMSE ---
    v3b_params = {**v3_params}
    # CatBoost Huber loss: more robust to outliers than RMSE
    # We need to use a custom approach since CatBoost Huber loss works on raw values
    # Actually for log-target, RMSE on log is already quite robust
    # Let's try with stronger regularization instead
    v3c_params = {
        "iterations": 3000,
        "learning_rate": 0.015,
        "depth": 7,
        "l2_leaf_reg": 8.0,
        "min_data_in_leaf": 15,
        "verbose": 0,
        "random_seed": 42,
        "early_stopping_rounds": 200,
        "bagging_temperature": 0.8,
        "random_strength": 1.5,
    }

    v3c = cross_validate(
        df,
        features=v3_features,
        cat_features=v3_cat_features,
        params=v3c_params,
        label="V3c: +regularization, bagging, depth=7, 3000 iters",
    )
    print_results(v3c)

    # Print comparison
    print(f"\n{'=' * 60}")
    print(f"  COMPARISON")
    print(f"{'=' * 60}")
    for r in [baseline, v3, v3c]:
        print(f"  {r['label']:55s} → MAPE {r['overall_mape']:.2f}%  (CV {r['cv_mape']:.2f}%)")


if __name__ == "__main__":
    asyncio.run(main())
