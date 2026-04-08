"""Fast MAPE evaluation — 3-fold CV with reduced iterations for quick comparison."""

from __future__ import annotations

import asyncio
import math
import sys
import time
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor, Pool
from sqlalchemy import text

sys.path.insert(0, ".")
from app.db.session import async_session_factory


PREMIUM_BRANDS = frozenset(
    {
        "bmw",
        "mercedes-benz",
        "mercedes",
        "audi",
        "lexus",
        "porsche",
        "infiniti",
        "jaguar",
        "land rover",
        "volvo",
        "cadillac",
    }
)

CURRENT_YEAR = 2026


async def load_data() -> pd.DataFrame:
    """Load listings via raw SQL to avoid ORM column issues."""
    async with async_session_factory() as session:
        r = await session.execute(
            text("""
            SELECT brand, model, year, mileage, price, source, city,
                   engine_type, transmission, drive_type, body_type,
                   engine_volume, power_hp, owners_count, photo_count,
                   is_dealer, listing_date, created_at,
                   color, steering_wheel, pts_type, seller_type,
                   condition, generation, region
            FROM listings
            WHERE is_duplicate = false AND price > 0
        """)
        )
        rows = r.all()

    records = []
    for r in rows:
        records.append(
            {
                "brand": r[0] or "unknown",
                "model": r[1] or "unknown",
                "year": r[2] or 2020,
                "mileage": r[3] or 0,
                "price": r[4],
                "source": r[5] or "unknown",
                "city": r[6] or "unknown",
                "engine_type": r[7] or "unknown",
                "transmission": r[8] or "unknown",
                "drive_type": r[9] or "unknown",
                "body_type": r[10] or "unknown",
                "engine_volume": r[11] or 0.0,
                "power_hp": r[12] or 0,
                "owners_count": r[13] or 0,
                "photo_count": r[14] or 0,
                "is_dealer": int(r[15]) if r[15] else 0,
                "listing_date": r[16],
                "created_at": r[17],
                "color": r[18] or "unknown",
                "steering_wheel": r[19] or "unknown",
                "pts_type": r[20] or "unknown",
                "seller_type": r[21] or "unknown",
                "condition": r[22] or "unknown",
                "generation": r[23] or "unknown",
                "region": r[24] or "unknown",
            }
        )
    return pd.DataFrame(records)


def clean_data(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df = df[(df["price"] >= 50_000) & (df["price"] <= 20_000_000)]
    df = df[(df["year"] >= 1990) & (df["year"] <= 2027)]

    cutoff = datetime.now(UTC) - timedelta(days=60)
    df["created_at"] = pd.to_datetime(df["created_at"], utc=True)
    df = df[df["created_at"] >= cutoff].copy()

    # IQR
    parts = []
    small = []
    removed = 0
    for _, g in df.groupby(["brand", "model"]):
        if len(g) < 5:
            small.append(g)
            continue
        q1, q3 = g["price"].quantile(0.25), g["price"].quantile(0.75)
        iqr = q3 - q1
        m = (g["price"] >= q1 - 1.5 * iqr) & (g["price"] <= q3 + 1.5 * iqr)
        removed += int((~m).sum())
        parts.append(g[m])
    df = pd.concat(parts + small, ignore_index=True)

    # MAD
    mad_parts = []
    mad_removed = 0
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
        m = z <= 3.5
        mad_removed += int((~m).sum())
        mad_parts.append(g[m])
    df = pd.concat(mad_parts, ignore_index=True)

    print(f"Cleaned: {len(df)} rows (IQR -{removed}, MAD -{mad_removed})")
    df = df.sample(frac=1.0, random_state=42).reset_index(drop=True)
    return df


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add all derived features."""
    df = df.copy()

    # Fill cat defaults
    for col in [
        "brand",
        "model",
        "source",
        "city",
        "engine_type",
        "transmission",
        "drive_type",
        "body_type",
        "color",
        "steering_wheel",
        "pts_type",
        "seller_type",
        "condition",
        "generation",
        "region",
    ]:
        if col in df.columns:
            df[col] = df[col].fillna("unknown").astype(str)

    # Fill num defaults
    for col, default in [
        ("year", 2020),
        ("mileage", 0),
        ("engine_volume", 0.0),
        ("power_hp", 0),
        ("owners_count", 0),
        ("photo_count", 0),
        ("is_dealer", 0),
    ]:
        if col in df.columns:
            df[col] = df[col].fillna(default)

    # Derived
    year = df["year"].astype(int)
    mileage = df["mileage"].astype(float)
    car_age = CURRENT_YEAR - year

    df["car_age"] = car_age
    df["log_car_age"] = car_age.apply(lambda a: math.log(a + 1))
    df["log_mileage"] = mileage.apply(lambda m: math.log(m + 1))
    df["mileage_per_year"] = mileage / car_age.clip(lower=1)
    df["mileage_ratio"] = mileage / (car_age * 15_000 + 1)

    if "listing_date" in df.columns:
        ld = pd.to_datetime(df["listing_date"], errors="coerce", utc=True)
        df["listing_month"] = ld.dt.month.fillna(0).astype(int)
    else:
        df["listing_month"] = 0

    engine_vol = df["engine_volume"].astype(float)
    power = df["power_hp"].astype(float)
    df["power_per_liter"] = power / engine_vol.clip(lower=0.1)

    brand_lower = df["brand"].astype(str).str.lower()
    df["is_premium"] = brand_lower.isin(PREMIUM_BRANDS).astype(int)

    # New features for V3
    df["brand_model"] = df["brand"].astype(str) + "_" + df["model"].astype(str)
    df["log_engine_volume"] = engine_vol.clip(lower=0.1).apply(lambda x: math.log(x))
    df["price_per_year"] = df["price"].astype(float) / car_age.clip(lower=1).astype(float)
    df["log_price_per_year"] = df["price_per_year"].clip(lower=1).apply(lambda x: math.log(x))

    return df


def cv_mape(df, features, cat_features, params, n_folds=3, label=""):
    """Quick 3-fold CV."""
    X = df[features]
    y = df["price"].values.astype(float)

    now = datetime.now(UTC)
    ref = pd.to_datetime(df["created_at"], utc=True).fillna(now)
    days_old = (now - ref).dt.total_seconds() / 86400.0
    weights = np.exp(-0.007 * days_old.values)

    cat_idx = [features.index(f) for f in cat_features if f in features]
    fold_size = len(X) // n_folds

    all_preds = np.zeros(len(X))
    all_actual = y.copy()
    t0 = time.time()

    for fold in range(n_folds):
        vs, ve = fold * fold_size, (fold + 1) * fold_size if fold < n_folds - 1 else len(X)

        Xt = pd.concat([X.iloc[:vs], X.iloc[ve:]])
        yt = np.log(np.concatenate([y[:vs], y[ve:]]))
        wt = np.concatenate([weights[:vs], weights[ve:]])

        Xv = X.iloc[vs:ve]
        yv_log = np.log(y[vs:ve])

        m = CatBoostRegressor(**params, loss_function="RMSE", eval_metric="MAPE", cat_features=cat_idx)
        tp = Pool(Xt, yt, cat_features=cat_idx, weight=wt)
        vp = Pool(Xv, yv_log, cat_features=cat_idx)
        m.fit(tp, eval_set=vp, verbose=0)

        all_preds[vs:ve] = np.exp(m.predict(Xv))
        print(f"  fold {fold + 1}/{n_folds} done ({time.time() - t0:.0f}s)")

    elapsed = time.time() - t0
    mape = float(np.mean(np.abs((all_actual - all_preds) / all_actual)) * 100)

    # Segment MAPEs
    segs = {}
    for name, lo, hi in [("<800K", 0, 800_000), ("800K-1.5M", 800_000, 1_500_000), (">1.5M", 1_500_000, 1e9)]:
        mask = (all_actual >= lo) & (all_actual < hi)
        if mask.sum() > 20:
            segs[name] = round(float(np.mean(np.abs((all_actual[mask] - all_preds[mask]) / all_actual[mask])) * 100), 1)

    # Brand MAPEs (top 5 worst)
    brands = df["brand"].values
    brand_m = {}
    for b in sorted(set(brands)):
        mask = brands == b
        if mask.sum() >= 30:
            bm = float(np.mean(np.abs((all_actual[mask] - all_preds[mask]) / all_actual[mask])) * 100)
            brand_m[b] = round(bm, 1)

    print(f"\n  [{label}] MAPE={mape:.2f}% ({elapsed:.0f}s, {len(features)} feats)")
    print(f"  Segments: {segs}")
    worst = sorted(brand_m.items(), key=lambda x: -x[1])[:7]
    print(f"  Worst brands: {worst}")
    best = sorted(brand_m.items(), key=lambda x: x[1])[:5]
    print(f"  Best brands:  {best}")

    # Feature importance from last fold
    imp = dict(zip(features, m.get_feature_importance(), strict=False))
    top_imp = sorted(imp.items(), key=lambda x: -x[1])[:10]
    print(f"  Top features: {[(k, round(v, 1)) for k, v in top_imp]}")

    return mape


async def main():
    print("Loading data...")
    df = await load_data()
    print(f"Loaded {len(df)} listings")
    df = clean_data(df)
    df = add_features(df)

    # ── Baseline ──
    base_cat = ["brand", "model", "source", "city", "engine_type", "transmission", "drive_type", "body_type"]
    base_num = [
        "year",
        "mileage",
        "engine_volume",
        "power_hp",
        "owners_count",
        "photo_count",
        "listing_month",
        "is_dealer",
        "is_premium",
        "car_age",
        "log_car_age",
        "log_mileage",
        "mileage_per_year",
        "mileage_ratio",
        "power_per_liter",
    ]
    base_feats = base_cat + base_num
    base_params = {
        "iterations": 1500,
        "learning_rate": 0.03,
        "depth": 8,
        "l2_leaf_reg": 5.0,
        "min_data_in_leaf": 10,
        "verbose": 0,
        "random_seed": 42,
        "early_stopping_rounds": 100,
    }

    print("\n=== BASELINE ===")
    m1 = cv_mape(df, base_feats, base_cat, base_params, label="baseline")

    # ── V3a: +brand_model + color ──
    v3a_cat = base_cat + ["brand_model", "color"]
    v3a_feats = v3a_cat + base_num
    print("\n=== V3a: +brand_model, +color ===")
    m2 = cv_mape(df, v3a_feats, v3a_cat, base_params, label="v3a")

    # ── V3b: +steering_wheel, pts_type, region ──
    v3b_cat = v3a_cat + ["steering_wheel", "pts_type", "region"]
    v3b_feats = v3b_cat + base_num + ["log_engine_volume"]
    print("\n=== V3b: +steering,pts,region,log_engine_vol ===")
    m3 = cv_mape(df, v3b_feats, v3b_cat, base_params, label="v3b")

    # ── V3c: tuned params ──
    v3c_params = {
        "iterations": 2000,
        "learning_rate": 0.02,
        "depth": 8,
        "l2_leaf_reg": 7.0,
        "min_data_in_leaf": 12,
        "verbose": 0,
        "random_seed": 42,
        "early_stopping_rounds": 150,
        "bagging_temperature": 0.8,
    }
    print("\n=== V3c: v3b + tuned (2000iter, lr=0.02, l2=7) ===")
    m4 = cv_mape(df, v3b_feats, v3b_cat, v3c_params, label="v3c")

    # ── V3d: depth=7, more regularization ──
    v3d_params = {
        "iterations": 2500,
        "learning_rate": 0.015,
        "depth": 7,
        "l2_leaf_reg": 10.0,
        "min_data_in_leaf": 20,
        "verbose": 0,
        "random_seed": 42,
        "early_stopping_rounds": 200,
        "bagging_temperature": 1.0,
        "random_strength": 2.0,
    }
    print("\n=== V3d: v3b + heavy reg (depth=7, l2=10, min_leaf=20) ===")
    m5 = cv_mape(df, v3b_feats, v3b_cat, v3d_params, label="v3d")

    print(f"\n{'=' * 60}")
    print(f"COMPARISON:")
    for name, mape in [
        ("baseline", m1),
        ("v3a +brand_model,color", m2),
        ("v3b +steering,pts,region", m3),
        ("v3c tuned", m4),
        ("v3d heavy_reg", m5),
    ]:
        delta = mape - m1
        print(f"  {name:35s} MAPE={mape:.2f}%  ({delta:+.2f}pp)")


if __name__ == "__main__":
    asyncio.run(main())
