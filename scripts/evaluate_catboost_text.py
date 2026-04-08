"""Evaluate CatBoost native text_features vs TF-IDF approach.

CatBoost can handle text natively via BoW in Pool(text_features=[...]).
Compare with our current TF-IDF+SVD approach.
"""

from __future__ import annotations

import asyncio
import math
import re
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
from app.services.description_features import DescriptionTfidf

PREMIUM_BRANDS = frozenset(
    {
        "bmw", "mercedes-benz", "mercedes", "audi", "lexus", "porsche",
        "infiniti", "jaguar", "land rover", "volvo", "cadillac",
    }
)
CURRENT_YEAR = 2026


def preprocess_text(text_val: str) -> str:
    if not text_val or pd.isna(text_val):
        return ""
    text_val = str(text_val).lower()
    text_val = re.sub(r"[^\w\s]", " ", text_val)
    text_val = re.sub(r"\s+", " ", text_val).strip()
    return text_val


async def load_data():
    async with async_session_factory() as session:
        r = await session.execute(text("""
            SELECT brand, model, year, mileage, price, source, city,
                   engine_type, transmission, drive_type, body_type,
                   engine_volume, power_hp, owners_count, photo_count,
                   is_dealer, listing_date, created_at, description
            FROM listings
            WHERE is_duplicate = false AND price > 0
        """))
        rows = r.all()
    cols = [
        "brand", "model", "year", "mileage", "price", "source", "city",
        "engine_type", "transmission", "drive_type", "body_type",
        "engine_volume", "power_hp", "owners_count", "photo_count",
        "is_dealer", "listing_date", "created_at", "description",
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
        mad_val = float(np.median(np.abs(lp - med)))
        if mad_val < 1e-9:
            mad_parts.append(g)
            continue
        z = np.abs(lp - med) / mad_val
        mad_parts.append(g[z <= 3.5])
    df = pd.concat(mad_parts, ignore_index=True)
    df = df.sample(frac=1.0, random_state=42).reset_index(drop=True)
    print(f"Cleaned: {len(df)} rows")
    return df


def add_features(df):
    df = df.copy()
    cats = ["brand", "model", "source", "city", "engine_type", "transmission", "drive_type", "body_type"]
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
    # Clean description for CatBoost text features
    df["description_clean"] = df["description"].fillna("").apply(preprocess_text)
    return df


def compute_mape(actual, pred):
    return float(np.mean(np.abs((actual - pred) / actual)) * 100)


async def main():
    print("Loading data...")
    df = await load_data()
    print(f"Loaded {len(df)}")
    df = clean_data(df)
    df = add_features(df)

    cat_feats = ["brand", "model", "source", "city", "engine_type", "transmission", "drive_type", "body_type"]
    num_feats = ["year", "mileage", "engine_volume", "power_hp", "owners_count", "photo_count",
                 "listing_month", "is_dealer", "is_premium", "car_age", "log_car_age", "log_mileage",
                 "mileage_per_year", "mileage_ratio", "power_per_liter"]

    base_feats = cat_feats + num_feats
    params = {"iterations": 1500, "learning_rate": 0.03, "depth": 8, "l2_leaf_reg": 5.0,
              "min_data_in_leaf": 10, "verbose": 0, "random_seed": 42, "early_stopping_rounds": 100}

    y = df["price"].values.astype(float)
    now = datetime.now(UTC)
    ref = pd.to_datetime(df["created_at"], utc=True).fillna(now)
    days_old = (now - ref).dt.total_seconds() / 86400.0
    weights = np.exp(-0.007 * days_old.values)

    kf = KFold(n_splits=3, shuffle=True, random_state=42)

    # Storage
    baseline_preds = np.zeros(len(df))
    tfidf_preds = np.zeros(len(df))
    native_preds = np.zeros(len(df))

    t0 = time.time()

    for fold_i, (train_idx, val_idx) in enumerate(kf.split(df)):
        print(f"\n--- Fold {fold_i + 1}/3 ---")
        y_train_log = np.log(y[train_idx])
        y_val_log = np.log(y[val_idx])
        w_train = weights[train_idx]

        # --- Baseline (no description) ---
        cat_idx = [base_feats.index(f) for f in cat_feats]
        X_base = df[base_feats]
        m = CatBoostRegressor(**params, loss_function="RMSE", eval_metric="MAPE", cat_features=cat_idx)
        tp = Pool(X_base.iloc[train_idx], y_train_log, cat_features=cat_idx, weight=w_train)
        vp = Pool(X_base.iloc[val_idx], y_val_log, cat_features=cat_idx)
        m.fit(tp, eval_set=vp, verbose=0)
        baseline_preds[val_idx] = np.exp(m.predict(X_base.iloc[val_idx]))
        print(f"  Baseline: MAPE={compute_mape(y[val_idx], baseline_preds[val_idx]):.2f}% ({time.time()-t0:.0f}s)")

        # --- TF-IDF approach ---
        tfidf = DescriptionTfidf(n_components=15)
        tfidf_train = tfidf.fit_transform(df["description"].fillna("").iloc[train_idx])
        tfidf_val = tfidf.transform(df["description"].fillna("").iloc[val_idx])
        tfidf_cols = list(tfidf_train.columns)

        # Build combined features
        X_tfidf_train = pd.concat([X_base.iloc[train_idx].reset_index(drop=True),
                                    tfidf_train.reset_index(drop=True)], axis=1)
        X_tfidf_val = pd.concat([X_base.iloc[val_idx].reset_index(drop=True),
                                  tfidf_val.reset_index(drop=True)], axis=1)
        tfidf_feats = base_feats + tfidf_cols

        m2 = CatBoostRegressor(**params, loss_function="RMSE", eval_metric="MAPE", cat_features=cat_idx)
        tp2 = Pool(X_tfidf_train, y_train_log, cat_features=cat_idx, weight=w_train)
        vp2 = Pool(X_tfidf_val, y_val_log, cat_features=cat_idx)
        m2.fit(tp2, eval_set=vp2, verbose=0)
        tfidf_preds[val_idx] = np.exp(m2.predict(X_tfidf_val))
        print(f"  TF-IDF: MAPE={compute_mape(y[val_idx], tfidf_preds[val_idx]):.2f}% ({time.time()-t0:.0f}s)")

        # --- CatBoost native text_features ---
        text_feats = base_feats + ["description_clean"]
        X_text = df[text_feats]

        text_processing = {
            "tokenizers": [{"tokenizer_id": "Space", "separator_type": "ByDelimiter",
                            "delimiter": " ", "lowercasing": "true"}],
            "dictionaries": [{"dictionary_id": "Word", "max_dictionary_size": "10000",
                              "occurrence_lower_bound": "5", "gram_order": "1"}],
            "feature_processing": {
                "default": [{"tokenizers_names": ["Space"], "dictionaries_names": ["Word"],
                             "feature_calcers": ["BoW:top_tokens_count=500"]}]
            },
        }

        m3 = CatBoostRegressor(**params, loss_function="RMSE", eval_metric="MAPE",
                                text_processing=text_processing)
        tp3 = Pool(X_text.iloc[train_idx], y_train_log,
                   cat_features=cat_feats, text_features=["description_clean"],
                   feature_names=text_feats, weight=w_train)
        vp3 = Pool(X_text.iloc[val_idx], y_val_log,
                   cat_features=cat_feats, text_features=["description_clean"],
                   feature_names=text_feats)
        m3.fit(tp3, eval_set=vp3, verbose=0)
        native_preds[val_idx] = np.exp(m3.predict(vp3))
        print(f"  Native text: MAPE={compute_mape(y[val_idx], native_preds[val_idx]):.2f}% ({time.time()-t0:.0f}s)")

    print(f"\n{'='*60}")
    print(f"FINAL COMPARISON ({time.time()-t0:.0f}s total)")
    print(f"{'='*60}")
    for name, preds in [("Baseline (no desc)", baseline_preds),
                         ("TF-IDF SVD-15", tfidf_preds),
                         ("CatBoost native text", native_preds)]:
        mape = compute_mape(y, preds)
        segs = {}
        for sn, lo, hi in [("<800K", 0, 800_000), ("800K-1.5M", 800_000, 1_500_000), (">1.5M", 1_500_000, 1e9)]:
            mask = (y >= lo) & (y < hi)
            if mask.sum() > 20:
                segs[sn] = round(compute_mape(y[mask], preds[mask]), 1)
        delta = mape - compute_mape(y, baseline_preds)
        print(f"  {name:25s} MAPE={mape:.2f}%  ({delta:+.2f}pp)  {segs}")


if __name__ == "__main__":
    asyncio.run(main())
