"""Stacking evaluation: CatBoost + LightGBM + target encoding.

Approach:
1. Target-encoded features: mean log(price) per (brand,model,year) with CV smoothing
2. CatBoost base model (current approach)
3. LightGBM base model (handles numerics differently)
4. Stacking: weighted blend of both models' predictions
"""

from __future__ import annotations

import asyncio
import math
import sys
import time
from datetime import UTC, datetime, timedelta

import lightgbm as lgb
import numpy as np
import pandas as pd
from catboost import CatBoostRegressor, Pool
from sklearn.model_selection import KFold
from sqlalchemy import text

sys.path.insert(0, ".")
from app.db.session import async_session_factory


PREMIUM_BRANDS = frozenset({
    "bmw", "mercedes-benz", "mercedes", "audi", "lexus", "porsche",
    "infiniti", "jaguar", "land rover", "volvo", "cadillac",
})
CURRENT_YEAR = 2026


async def load_data() -> pd.DataFrame:
    async with async_session_factory() as session:
        r = await session.execute(text("""
            SELECT brand, model, year, mileage, price, source, city,
                   engine_type, transmission, drive_type, body_type,
                   engine_volume, power_hp, owners_count, photo_count,
                   is_dealer, listing_date, created_at, color
            FROM listings
            WHERE is_duplicate = false AND price > 0
        """))
        rows = r.all()

    records = []
    for r in rows:
        records.append({
            "brand": r[0] or "unknown", "model": r[1] or "unknown",
            "year": r[2] or 2020, "mileage": r[3] or 0, "price": r[4],
            "source": r[5] or "unknown", "city": r[6] or "unknown",
            "engine_type": r[7] or "unknown", "transmission": r[8] or "unknown",
            "drive_type": r[9] or "unknown", "body_type": r[10] or "unknown",
            "engine_volume": r[11] or 0.0, "power_hp": r[12] or 0,
            "owners_count": r[13] or 0, "photo_count": r[14] or 0,
            "is_dealer": int(r[15]) if r[15] else 0,
            "listing_date": r[16], "created_at": r[17],
            "color": r[18] or "unknown",
        })
    return pd.DataFrame(records)


def clean_data(df):
    df = df.copy()
    df = df[(df["price"] >= 50_000) & (df["price"] <= 20_000_000)]
    df = df[(df["year"] >= 1990) & (df["year"] <= 2027)]
    cutoff = datetime.now(UTC) - timedelta(days=60)
    df["created_at"] = pd.to_datetime(df["created_at"], utc=True)
    df = df[df["created_at"] >= cutoff].copy()

    # IQR + MAD per brand+model
    parts, small = [], []
    for _, g in df.groupby(["brand", "model"]):
        if len(g) < 5:
            small.append(g)
            continue
        q1, q3 = g["price"].quantile(0.25), g["price"].quantile(0.75)
        iqr = q3 - q1
        m = (g["price"] >= q1 - 1.5 * iqr) & (g["price"] <= q3 + 1.5 * iqr)
        parts.append(g[m])
    df = pd.concat(parts + small, ignore_index=True)

    mad_parts = []
    for _, g in df.groupby(["brand", "model"]):
        if len(g) < 10:
            mad_parts.append(g)
            continue
        lp = np.log(g["price"].values.astype(float))
        med = float(np.median(lp))
        mad = float(np.median(np.abs(lp - med)))
        if mad < 1e-9:
            mad_parts.append(g)
            continue
        z = np.abs(lp - med) / mad
        mad_parts.append(g[z <= 3.5])
    df = pd.concat(mad_parts, ignore_index=True)
    df = df.sample(frac=1.0, random_state=42).reset_index(drop=True)
    print(f"Cleaned: {len(df)} rows")
    return df


def add_features(df):
    df = df.copy()
    cats = ["brand", "model", "source", "city", "engine_type", "transmission", "drive_type", "body_type", "color"]
    for c in cats:
        df[c] = df[c].fillna("unknown").astype(str)
    for c, d in [("year", 2020), ("mileage", 0), ("engine_volume", 0.0), ("power_hp", 0),
                  ("owners_count", 0), ("photo_count", 0), ("is_dealer", 0)]:
        df[c] = df[c].fillna(d)

    year = df["year"].astype(int)
    mileage = df["mileage"].astype(float)
    car_age = CURRENT_YEAR - year

    df["car_age"] = car_age
    df["log_car_age"] = np.log(car_age.values + 1)
    df["log_mileage"] = np.log(mileage.values + 1)
    df["mileage_per_year"] = mileage / car_age.clip(lower=1)
    df["mileage_ratio"] = mileage / (car_age * 15_000 + 1)

    ld = pd.to_datetime(df.get("listing_date"), errors="coerce", utc=True)
    df["listing_month"] = ld.dt.month.fillna(0).astype(int) if ld is not None else 0

    ev = df["engine_volume"].astype(float).clip(lower=0.1)
    pw = df["power_hp"].astype(float)
    df["power_per_liter"] = pw / ev
    df["is_premium"] = df["brand"].str.lower().isin(PREMIUM_BRANDS).astype(int)
    df["log_engine_volume"] = np.log(ev.values)

    return df


def target_encode_cv(df, col, target, n_folds=3, smoothing=50):
    """Cross-validated target encoding with smoothing."""
    global_mean = target.mean()
    encoded = np.full(len(df), global_mean)
    kf = KFold(n_splits=n_folds, shuffle=True, random_state=42)

    for train_idx, val_idx in kf.split(df):
        # Compute stats on train fold
        train_target = target.iloc[train_idx]
        train_col = df[col].iloc[train_idx]

        stats = train_target.groupby(train_col).agg(["mean", "count"])
        # Smoothed encoding: (count * mean + smoothing * global_mean) / (count + smoothing)
        smooth_mean = (stats["count"] * stats["mean"] + smoothing * global_mean) / (stats["count"] + smoothing)

        # Apply to val fold
        val_col = df[col].iloc[val_idx]
        encoded[val_idx] = val_col.map(smooth_mean).fillna(global_mean).values

    return encoded


def compute_mape(actual, pred):
    return float(np.mean(np.abs((actual - pred) / actual)) * 100)


def segment_mapes(actual, pred):
    result = {}
    for name, lo, hi in [("<800K", 0, 800_000), ("800K-1.5M", 800_000, 1_500_000), (">1.5M", 1_500_000, 1e9)]:
        m = (actual >= lo) & (actual < hi)
        if m.sum() > 20:
            result[name] = round(compute_mape(actual[m], pred[m]), 1)
    return result


def brand_mapes(brands, actual, pred, top_n=7):
    result = {}
    for b in sorted(set(brands)):
        m = brands == b
        if m.sum() >= 30:
            result[b] = round(compute_mape(actual[m], pred[m]), 1)
    worst = sorted(result.items(), key=lambda x: -x[1])[:top_n]
    best = sorted(result.items(), key=lambda x: x[1])[:5]
    return worst, best


async def main():
    print("Loading data...")
    df = await load_data()
    print(f"Loaded {len(df)}")
    df = clean_data(df)
    df = add_features(df)

    log_price = np.log(df["price"].values.astype(float))

    # Target encoding features (on log_price for consistency)
    print("Computing target encodings...")
    df["brand_model_key"] = df["brand"] + "_" + df["model"]
    df["brand_model_year_key"] = df["brand"] + "_" + df["model"] + "_" + df["year"].astype(str)

    log_price_series = pd.Series(log_price, index=df.index)
    df["te_brand_model"] = target_encode_cv(df, "brand_model_key", log_price_series, smoothing=30)
    df["te_brand_model_year"] = target_encode_cv(df, "brand_model_year_key", log_price_series, smoothing=10)
    df["te_brand"] = target_encode_cv(df, "brand", log_price_series, smoothing=50)
    df["te_city"] = target_encode_cv(df, "city", log_price_series, smoothing=50)

    # Feature sets
    cat_feats = ["brand", "model", "source", "city", "engine_type", "transmission", "drive_type", "body_type"]
    num_feats = ["year", "mileage", "engine_volume", "power_hp", "owners_count", "photo_count",
                 "listing_month", "is_dealer", "is_premium", "car_age", "log_car_age", "log_mileage",
                 "mileage_per_year", "mileage_ratio", "power_per_liter"]

    # LightGBM needs label-encoded categoricals
    for c in cat_feats:
        df[f"{c}_code"] = df[c].astype("category").cat.codes

    lgb_feats = [f"{c}_code" for c in cat_feats] + num_feats + [
        "te_brand_model", "te_brand_model_year", "te_brand", "te_city", "log_engine_volume"
    ]

    cb_feats = cat_feats + num_feats
    cb_feats_te = cat_feats + num_feats + ["te_brand_model", "te_brand_model_year", "te_brand", "te_city"]

    y = df["price"].values.astype(float)
    now = datetime.now(UTC)
    ref = pd.to_datetime(df["created_at"], utc=True).fillna(now)
    days_old = (now - ref).dt.total_seconds() / 86400.0
    weights = np.exp(-0.007 * days_old.values)

    kf = KFold(n_splits=3, shuffle=True, random_state=42)

    # Storage for predictions
    cb_preds = np.zeros(len(df))
    cb_te_preds = np.zeros(len(df))
    lgb_preds = np.zeros(len(df))
    stack_preds = np.zeros(len(df))

    cb_params = {"iterations": 1500, "learning_rate": 0.03, "depth": 8, "l2_leaf_reg": 5.0,
                 "min_data_in_leaf": 10, "verbose": 0, "random_seed": 42, "early_stopping_rounds": 100}

    lgb_params = {
        "objective": "regression", "metric": "mape", "learning_rate": 0.03,
        "num_leaves": 127, "max_depth": 8, "min_data_in_leaf": 10,
        "lambda_l2": 5.0, "feature_fraction": 0.8, "bagging_fraction": 0.8,
        "bagging_freq": 5, "verbose": -1, "seed": 42,
    }

    t0 = time.time()

    for fold_i, (train_idx, val_idx) in enumerate(kf.split(df)):
        print(f"\n--- Fold {fold_i+1}/3 ---")

        y_train_log = np.log(y[train_idx])
        y_val_log = np.log(y[val_idx])
        w_train = weights[train_idx]

        # --- CatBoost baseline ---
        cat_idx_base = [cb_feats.index(f) for f in cat_feats]
        X_cb_train = df[cb_feats].iloc[train_idx]
        X_cb_val = df[cb_feats].iloc[val_idx]

        cb_model = CatBoostRegressor(**cb_params, loss_function="RMSE", eval_metric="MAPE", cat_features=cat_idx_base)
        cb_model.fit(Pool(X_cb_train, y_train_log, cat_features=cat_idx_base, weight=w_train),
                     eval_set=Pool(X_cb_val, y_val_log, cat_features=cat_idx_base), verbose=0)
        cb_preds[val_idx] = np.exp(cb_model.predict(X_cb_val))
        print(f"  CatBoost: MAPE={compute_mape(y[val_idx], cb_preds[val_idx]):.2f}% ({time.time()-t0:.0f}s)")

        # --- CatBoost + target encoding ---
        cat_idx_te = [cb_feats_te.index(f) for f in cat_feats]
        X_cb_te_train = df[cb_feats_te].iloc[train_idx]
        X_cb_te_val = df[cb_feats_te].iloc[val_idx]

        cb_te_model = CatBoostRegressor(**cb_params, loss_function="RMSE", eval_metric="MAPE", cat_features=cat_idx_te)
        cb_te_model.fit(Pool(X_cb_te_train, y_train_log, cat_features=cat_idx_te, weight=w_train),
                        eval_set=Pool(X_cb_te_val, y_val_log, cat_features=cat_idx_te), verbose=0)
        cb_te_preds[val_idx] = np.exp(cb_te_model.predict(X_cb_te_val))
        print(f"  CatBoost+TE: MAPE={compute_mape(y[val_idx], cb_te_preds[val_idx]):.2f}% ({time.time()-t0:.0f}s)")

        # --- LightGBM ---
        X_lgb_train = df[lgb_feats].iloc[train_idx]
        X_lgb_val = df[lgb_feats].iloc[val_idx]

        lgb_train = lgb.Dataset(X_lgb_train, y_train_log, weight=w_train)
        lgb_val = lgb.Dataset(X_lgb_val, y_val_log, reference=lgb_train)
        lgb_model = lgb.train(lgb_params, lgb_train, num_boost_round=1500,
                              valid_sets=[lgb_val],
                              callbacks=[lgb.early_stopping(100), lgb.log_evaluation(0)])
        lgb_preds[val_idx] = np.exp(lgb_model.predict(X_lgb_val))
        print(f"  LightGBM: MAPE={compute_mape(y[val_idx], lgb_preds[val_idx]):.2f}% ({time.time()-t0:.0f}s)")

        # --- Stacking (simple weighted blend) ---
        # Try different weights
        best_w = None
        best_mape = 999
        for w_cb in np.arange(0.2, 0.85, 0.05):
            for w_lgb in np.arange(0.15, 0.8 - w_cb + 0.05, 0.05):
                w_cb_te = 1.0 - w_cb - w_lgb
                if w_cb_te < 0:
                    continue
                blend = w_cb * cb_preds[val_idx] + w_lgb * lgb_preds[val_idx] + w_cb_te * cb_te_preds[val_idx]
                m = compute_mape(y[val_idx], blend)
                if m < best_mape:
                    best_mape = m
                    best_w = (w_cb, w_lgb, w_cb_te)

        stack_preds[val_idx] = best_w[0] * cb_preds[val_idx] + best_w[1] * lgb_preds[val_idx] + best_w[2] * cb_te_preds[val_idx]
        print(f"  Stack: MAPE={best_mape:.2f}% weights=CB:{best_w[0]:.2f} LGB:{best_w[1]:.2f} CB+TE:{best_w[2]:.2f}")

    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"FINAL RESULTS ({elapsed:.0f}s)")
    print(f"{'='*60}")

    for name, preds in [("CatBoost", cb_preds), ("CatBoost+TE", cb_te_preds),
                         ("LightGBM", lgb_preds), ("Stacking", stack_preds)]:
        mape = compute_mape(y, preds)
        segs = segment_mapes(y, preds)
        worst, best = brand_mapes(df["brand"].values, y, preds)
        print(f"\n  [{name}] MAPE={mape:.2f}%")
        print(f"    Segments: {segs}")
        print(f"    Worst: {worst}")
        print(f"    Best:  {best}")


if __name__ == "__main__":
    asyncio.run(main())
