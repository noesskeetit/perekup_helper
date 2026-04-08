"""Evaluate impact of description features on MAPE."""

from __future__ import annotations

import asyncio
import math
import sys
import time
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor, Pool
from sklearn.model_selection import KFold
from sqlalchemy import text

sys.path.insert(0, ".")
from app.db.session import async_session_factory
from app.services.description_features import DescriptionTfidf, extract_keywords

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


async def load_data():
    async with async_session_factory() as session:
        r = await session.execute(
            text("""
            SELECT brand, model, year, mileage, price, source, city,
                   engine_type, transmission, drive_type, body_type,
                   engine_volume, power_hp, owners_count, photo_count,
                   is_dealer, listing_date, created_at, description
            FROM listings
            WHERE is_duplicate = false AND price > 0
        """)
        )
        rows = r.all()
    cols = [
        "brand",
        "model",
        "year",
        "mileage",
        "price",
        "source",
        "city",
        "engine_type",
        "transmission",
        "drive_type",
        "body_type",
        "engine_volume",
        "power_hp",
        "owners_count",
        "photo_count",
        "is_dealer",
        "listing_date",
        "created_at",
        "description",
    ]
    return pd.DataFrame([dict(zip(cols, r)) for r in rows])


def clean_data(df):
    df = df.copy()
    df = df[(df["price"] >= 50_000) & (df["price"] <= 20_000_000)]
    df = df[(df["year"] >= 1990) & (df["year"] <= 2027)]
    cutoff = datetime.now(UTC) - timedelta(days=60)
    df["created_at"] = pd.to_datetime(df["created_at"], utc=True)
    df = df[df["created_at"] >= cutoff].copy()

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


def add_base_features(df):
    df = df.copy()
    cats = ["brand", "model", "source", "city", "engine_type", "transmission", "drive_type", "body_type"]
    for c in cats:
        df[c] = df[c].fillna("unknown").astype(str)
    for c, d in [
        ("year", 2020),
        ("mileage", 0),
        ("engine_volume", 0.0),
        ("power_hp", 0),
        ("owners_count", 0),
        ("photo_count", 0),
        ("is_dealer", 0),
    ]:
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
    return df


def compute_mape(actual, pred):
    return float(np.mean(np.abs((actual - pred) / actual)) * 100)


def cv_mape(df, features, cat_features, params, n_folds=3, label=""):
    X = df[features]
    y = df["price"].values.astype(float)
    now = datetime.now(UTC)
    ref = pd.to_datetime(df["created_at"], utc=True).fillna(now)
    days_old = (now - ref).dt.total_seconds() / 86400.0
    weights = np.exp(-0.007 * days_old.values)
    cat_idx = [features.index(f) for f in cat_features if f in features]

    kf = KFold(n_splits=n_folds, shuffle=True, random_state=42)
    all_preds = np.zeros(len(X))
    t0 = time.time()

    for fold_i, (train_idx, val_idx) in enumerate(kf.split(X)):
        y_train_log = np.log(y[train_idx])
        y_val_log = np.log(y[val_idx])
        w_train = weights[train_idx]

        m = CatBoostRegressor(**params, loss_function="RMSE", eval_metric="MAPE", cat_features=cat_idx)
        tp = Pool(X.iloc[train_idx], y_train_log, cat_features=cat_idx, weight=w_train)
        vp = Pool(X.iloc[val_idx], y_val_log, cat_features=cat_idx)
        m.fit(tp, eval_set=vp, verbose=0)
        all_preds[val_idx] = np.exp(m.predict(X.iloc[val_idx]))
        print(f"  fold {fold_i + 1}/{n_folds} done ({time.time() - t0:.0f}s)")

    mape = compute_mape(y, all_preds)

    segs = {}
    for name, lo, hi in [("<800K", 0, 800_000), ("800K-1.5M", 800_000, 1_500_000), (">1.5M", 1_500_000, 1e9)]:
        mask = (y >= lo) & (y < hi)
        if mask.sum() > 20:
            segs[name] = round(compute_mape(y[mask], all_preds[mask]), 1)

    # Feature importance from last fold
    imp = dict(zip(features, m.get_feature_importance(), strict=False))
    top_imp = sorted(imp.items(), key=lambda x: -x[1])[:15]

    print(f"\n  [{label}] MAPE={mape:.2f}% ({time.time() - t0:.0f}s)")
    print(f"  Segments: {segs}")
    print(f"  Top features: {[(k, round(v, 1)) for k, v in top_imp]}")
    return mape


async def main():
    print("Loading data...")
    df = await load_data()
    print(f"Loaded {len(df)}")
    df = clean_data(df)
    df = add_base_features(df)

    # Base features
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

    params = {
        "iterations": 1500,
        "learning_rate": 0.03,
        "depth": 8,
        "l2_leaf_reg": 5.0,
        "min_data_in_leaf": 10,
        "verbose": 0,
        "random_seed": 42,
        "early_stopping_rounds": 100,
    }

    # ── Baseline ──
    print("\n=== BASELINE ===")
    m1 = cv_mape(df, base_feats, base_cat, params, label="baseline")

    # ── +Keywords only ──
    print("\nExtracting keywords...")
    kw_df = extract_keywords(df["description"].fillna(""))
    # Only keep keywords that appear in >1% of listings
    useful_kw = [c for c in kw_df.columns if kw_df[c].mean() > 0.01]
    print(f"Using {len(useful_kw)} keyword features: {useful_kw}")
    for c in useful_kw:
        df[c] = kw_df[c]
    kw_feats = base_feats + useful_kw

    print("\n=== +KEYWORDS ===")
    m2 = cv_mape(df, kw_feats, base_cat, params, label="+keywords")

    # ── +TF-IDF only ──
    print("\nFitting TF-IDF...")
    tfidf = DescriptionTfidf(n_components=15)
    # Need cross-validated TF-IDF to avoid leakage
    # Simple approach: fit on full data (minor leakage but acceptable for evaluation)
    tfidf_df = tfidf.fit_transform(df["description"].fillna(""))
    tfidf_cols = list(tfidf_df.columns)
    for c in tfidf_cols:
        df[c] = tfidf_df[c]
    tfidf_feats = base_feats + tfidf_cols

    print("\n=== +TF-IDF ===")
    m3 = cv_mape(df, tfidf_feats, base_cat, params, label="+tfidf")

    # ── +Keywords + TF-IDF ──
    both_feats = base_feats + useful_kw + tfidf_cols
    print("\n=== +KEYWORDS + TF-IDF ===")
    m4 = cv_mape(df, both_feats, base_cat, params, label="+kw+tfidf")

    print(f"\n{'=' * 60}")
    print(f"COMPARISON:")
    for name, mape in [("baseline", m1), ("+keywords", m2), ("+tfidf", m3), ("+kw+tfidf", m4)]:
        delta = mape - m1
        print(f"  {name:25s} MAPE={mape:.2f}%  ({delta:+.2f}pp)")


if __name__ == "__main__":
    asyncio.run(main())
