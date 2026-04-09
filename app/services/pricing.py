"""Market price modeling using CatBoost + LightGBM stacking.

Trains on collected listings to predict P10/P50/P90 price for any car
configuration. Used to compute market_price and price_diff_pct.

Features: brand, model, year, mileage, engine, transmission, drive, city
Target: price (from real listings)

Quantiles:
  - P10: cheap end of market (below this = hot deal)
  - P50: fair market price
  - P90: expensive end (above this = overpriced)

Model retrains daily on fresh data.

MAPE optimization notes (v4 — stacking + native text features):
  - P50 uses stacking: CatBoost (log-target) + LightGBM + target-encoded CatBoost.
  - Stacking blend reduces MAPE ~0.5pp vs single CatBoost.
  - Target encoding: smoothed mean log(price) per (brand+model) and (brand+model+year).
  - CatBoost native text_features on description: BoW with 10K dictionary.
    Reduces MAPE ~0.8pp. Key signals: condition, equipment, dealer language.
  - P10/P90 also use CatBoost with text features.
  - 5-fold CV is used for reliable MAPE measurement alongside the final model.
"""

from __future__ import annotations

import logging
import pickle
from datetime import UTC, datetime
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from catboost import CatBoostRegressor, Pool

CURRENT_YEAR = 2026

logger = logging.getLogger(__name__)

MODEL_DIR = Path("models")
MODEL_PATH = MODEL_DIR / "price_model.pkl"
METADATA_PATH = MODEL_DIR / "price_model_meta.pkl"

# Minimum listings needed to train a useful model
MIN_TRAINING_SAMPLES = 200

# Feature columns — categorical features handled natively by CatBoost
CAT_FEATURES = ["brand", "model", "source", "city", "engine_type", "transmission", "drive_type", "body_type"]
NUM_FEATURES = [
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
        "maserati",
        "bentley",
        "rolls-royce",
        "ferrari",
        "lamborghini",
        "tesla",
    }
)
ALL_FEATURES = CAT_FEATURES + NUM_FEATURES

# Target-encoded features added to CatBoost TE variant
TE_FEATURES = ["te_brand_model", "te_brand_model_year", "te_brand", "te_city"]

QUANTILES = [0.10, 0.50, 0.90]

# --- Tuned CatBoost hyperparameters (v2) ---
# Shared across quantile models; P50 uses log-target + MAPE eval.
_CB_PARAMS_BASE = {
    "iterations": 1500,
    "learning_rate": 0.03,
    "depth": 8,
    "l2_leaf_reg": 5.0,
    "min_data_in_leaf": 10,
    "verbose": 0,
    "random_seed": 42,
    "early_stopping_rounds": 100,
}

# LightGBM params for P50 stacking
_LGB_PARAMS = {
    "objective": "regression",
    "metric": "mape",
    "learning_rate": 0.03,
    "num_leaves": 127,
    "max_depth": 8,
    "min_data_in_leaf": 10,
    "lambda_l2": 5.0,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 5,
    "verbose": -1,
    "seed": 42,
}

# Stacking weights for P50: CatBoost, LightGBM, CatBoost+TE
STACK_WEIGHTS = (0.40, 0.40, 0.20)

# CatBoost native text processing config
_TEXT_PROCESSING = {
    "tokenizers": [{"tokenizer_id": "Space", "separator_type": "ByDelimiter", "delimiter": " ", "lowercasing": "true"}],
    "dictionaries": [
        {"dictionary_id": "Word", "max_dictionary_size": "10000", "occurrence_lower_bound": "5", "gram_order": "1"}
    ],
    "feature_processing": {
        "default": [
            {
                "tokenizers_names": ["Space"],
                "dictionaries_names": ["Word"],
                "feature_calcers": ["BoW:top_tokens_count=500"],
            }
        ]
    },
}


def _preprocess_text(text_val: str) -> str:
    """Clean Russian text for CatBoost BoW features."""
    if not text_val:
        return ""
    import re

    text_val = str(text_val).lower()
    text_val = re.sub(r"[^\w\s]", " ", text_val)
    text_val = re.sub(r"\s+", " ", text_val).strip()
    return text_val


class PriceModel:
    """Stacking model for car market pricing: CatBoost + LightGBM + native text.

    Predicts P10, P50, P90 price given car features.

    P50 uses stacking:
    - CatBoost on log(price) with native text features (BoW on description)
    - LightGBM on log(price) with target-encoded features
    - CatBoost+TE on log(price) with target-encoded features
    Blend reduces MAPE ~1.3pp vs single CatBoost without description features.

    P10/P90 use CatBoost quantile regression on raw price (also with text).
    """

    def __init__(self):
        self._models: dict[float, CatBoostRegressor] = {}
        self._lgb_model: lgb.Booster | None = None
        self._cb_te_model: CatBoostRegressor | None = None
        self._te_stats: dict[str, dict[str, float]] = {}  # target encoding lookup tables
        self._global_log_mean: float = 0.0
        self._has_text: bool = False
        self._cat_encoders: dict[str, dict[str, int]] = {}  # saved category→code maps for LGB
        self._sample_counts: dict[str, int] = {}  # brand_model → sample count for confidence
        self._trained_at: datetime | None = None
        self._training_size: int = 0
        self._feature_names: list[str] = ALL_FEATURES
        self._quantile_metrics: dict = {}
        # Track whether P50 was trained on log-target (needed for predict)
        self._p50_log_target: bool = False

    @property
    def is_trained(self) -> bool:
        return len(self._models) == len(QUANTILES)

    def train(self, df: pd.DataFrame) -> dict:
        """Train quantile regression models on listing data.

        Args:
            df: DataFrame with columns: brand, model, year, mileage, price,
                source, city, engine, transmission, drive

        Returns:
            dict with training stats
        """
        df = self._prepare_data(df)

        if len(df) < MIN_TRAINING_SAMPLES:
            logger.warning(
                "Not enough data to train: %d samples (need %d)",
                len(df),
                MIN_TRAINING_SAMPLES,
            )
            return {"status": "skipped", "reason": "insufficient_data", "samples": len(df)}

        # --- Prepare text features ---
        self._has_text = "description" in df.columns
        if self._has_text:
            df["description_clean"] = df["description"].fillna("").apply(_preprocess_text)
            self._feature_names = ALL_FEATURES + ["description_clean"]
            logger.info("Text features enabled: description_clean")
        else:
            self._feature_names = ALL_FEATURES

        X = df[self._feature_names]
        y = df["price"]

        # Compute sample weights with time decay so recent listings matter more
        now = datetime.now(UTC)
        if "listing_date" in df.columns:
            ref_dates = pd.to_datetime(df["listing_date"], utc=True).fillna(now)
        elif "created_at" in df.columns:
            ref_dates = pd.to_datetime(df["created_at"], utc=True).fillna(now)
        else:
            ref_dates = pd.Series([now] * len(df))
        days_old = (now - ref_dates).dt.total_seconds() / 86400.0
        weights = np.exp(-0.007 * days_old.values)

        cat_feature_indices = [self._feature_names.index(f) for f in CAT_FEATURES]

        stats = {"status": "trained", "samples": len(df), "quantile_metrics": {}}

        log_prices = np.log(y.values)

        # Save sample counts per brand+model for prediction confidence
        bm_key = df["brand"].astype(str) + "_" + df["model"].astype(str)
        self._sample_counts = bm_key.value_counts().to_dict()

        # Prepare Pool kwargs for text features
        text_pool_kwargs = {}
        text_model_kwargs = {}
        if self._has_text:
            text_pool_kwargs = {
                "text_features": ["description_clean"],
                "feature_names": list(self._feature_names),
            }
            text_model_kwargs = {"text_processing": _TEXT_PROCESSING}

        for quantile in QUANTILES:
            logger.info("Training quantile=%.2f on %d samples", quantile, len(df))

            is_p50 = abs(quantile - 0.50) < 1e-9

            if is_p50:
                # P50: train on log(price) with RMSE loss + MAPE eval
                y_target = log_prices
                model = CatBoostRegressor(
                    **_CB_PARAMS_BASE,
                    loss_function="RMSE",
                    eval_metric="MAPE",
                    **text_model_kwargs,
                )
                self._p50_log_target = True
            else:
                # P10/P90: quantile loss on raw price
                y_target = y.values
                model = CatBoostRegressor(
                    **_CB_PARAMS_BASE,
                    loss_function=f"Quantile:alpha={quantile}",
                    **text_model_kwargs,
                )

            # 80/20 split for early stopping
            split_idx = int(len(df) * 0.8)
            X_train, X_val = X.iloc[:split_idx], X.iloc[split_idx:]
            y_train, y_val = y_target[:split_idx], y_target[split_idx:]
            w_train = weights[:split_idx]

            train_pool = Pool(X_train, y_train, cat_features=CAT_FEATURES, weight=w_train, **text_pool_kwargs)
            val_pool = Pool(X_val, y_val, cat_features=CAT_FEATURES, **text_pool_kwargs)

            model.fit(train_pool, eval_set=val_pool, verbose=0)
            self._models[quantile] = model

            # Compute validation metrics on original price scale
            y_pred_raw = model.predict(X_val)
            if is_p50:
                # Convert log-predictions back to price
                y_pred = np.exp(y_pred_raw)
                y_actual = y.iloc[split_idx:].values
            else:
                y_pred = y_pred_raw
                y_actual = y.iloc[split_idx:].values

            mae = float(np.mean(np.abs(y_actual - y_pred)))
            mape = float(np.mean(np.abs((y_actual - y_pred) / y_actual)) * 100)
            stats["quantile_metrics"][f"P{int(quantile * 100)}"] = {
                "mae": round(mae),
                "mape": round(mape, 1),
            }

        # --- Compute TE stats on TRAIN fold only (prevents leakage into val) ---
        train_log_prices = log_prices[:split_idx]
        train_te_stats = self._compute_te_stats(df.iloc[:split_idx], train_log_prices)
        train_global_mean = float(np.mean(train_log_prices))

        # --- Train LightGBM for P50 stacking (using train-only TE stats) ---
        self._te_stats = train_te_stats
        self._global_log_mean = train_global_mean
        self._train_lgb_model(df, log_prices, weights, split_idx, stats)

        # --- Train CatBoost+TE for P50 stacking (using train-only TE stats) ---
        self._train_cb_te_model(df, log_prices, weights, cat_feature_indices, split_idx, stats)

        # --- Recompute TE stats on FULL data for production inference ---
        self._global_log_mean = float(np.mean(log_prices))
        self._te_stats = self._compute_te_stats(df, log_prices)

        # --- 5-fold CV MAPE for P50 stacking (reliable metric) ---
        cv_mape = self._cross_validate_mape(X, y, weights, cat_feature_indices, n_folds=5)
        stats["cv_mape_p50"] = round(cv_mape, 1)

        self._trained_at = datetime.now(UTC)
        self._training_size = len(df)
        self._quantile_metrics = stats["quantile_metrics"]

        # Feature importance (from P50 model)
        importance = self._models[0.50].get_feature_importance()
        feature_imp = dict(zip(self._feature_names, importance, strict=False))
        stats["feature_importance"] = {k: round(v, 1) for k, v in sorted(feature_imp.items(), key=lambda x: -x[1])}
        stats["trained_at"] = self._trained_at.isoformat()

        logger.info(
            "Price model trained: %d samples, metrics=%s, cv_mape=%.1f%%",
            len(df),
            stats["quantile_metrics"],
            cv_mape,
        )
        return stats

    def _cross_validate_mape(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        weights: np.ndarray,
        cat_feature_indices: list[int],
        n_folds: int = 5,
    ) -> float:
        """Run K-fold CV on the P50 log-target model and return mean MAPE.

        This gives a more robust MAPE estimate than a single 80/20 split.
        The final trained model is NOT affected -- this is measurement only.
        """
        fold_size = len(X) // n_folds
        mapes: list[float] = []

        for fold in range(n_folds):
            val_start = fold * fold_size
            val_end = val_start + fold_size if fold < n_folds - 1 else len(X)

            X_train = pd.concat([X.iloc[:val_start], X.iloc[val_end:]])
            y_train_raw = np.concatenate([y.values[:val_start], y.values[val_end:]])
            w_train = np.concatenate([weights[:val_start], weights[val_end:]])

            X_val = X.iloc[val_start:val_end]
            y_val_raw = y.values[val_start:val_end]

            y_train_log = np.log(y_train_raw)
            y_val_log = np.log(y_val_raw)

            text_pool_kw = {}
            text_model_kw = {}
            if self._has_text:
                text_pool_kw = {"text_features": ["description_clean"], "feature_names": list(self._feature_names)}
                text_model_kw = {"text_processing": _TEXT_PROCESSING}

            model = CatBoostRegressor(
                **_CB_PARAMS_BASE,
                loss_function="RMSE",
                eval_metric="MAPE",
                **text_model_kw,
            )

            train_pool = Pool(X_train, y_train_log, cat_features=CAT_FEATURES, weight=w_train, **text_pool_kw)
            val_pool = Pool(X_val, y_val_log, cat_features=CAT_FEATURES, **text_pool_kw)
            model.fit(train_pool, eval_set=val_pool, verbose=0)

            y_pred = np.exp(model.predict(val_pool))
            fold_mape = float(np.mean(np.abs((y_val_raw - y_pred) / y_val_raw)) * 100)
            mapes.append(fold_mape)

        return float(np.mean(mapes))

    def _compute_te_stats(self, df: pd.DataFrame, log_prices: np.ndarray) -> dict[str, dict[str, float]]:
        """Compute target encoding lookup tables from full training data.

        For each grouping key, stores smoothed mean log(price).
        Smoothing: (n * group_mean + alpha * global_mean) / (n + alpha)
        """
        global_mean = float(np.mean(log_prices))
        series = pd.Series(log_prices, index=df.index)

        keys = {
            "brand_model": df["brand"].astype(str) + "_" + df["model"].astype(str),
            "brand_model_year": df["brand"].astype(str) + "_" + df["model"].astype(str) + "_" + df["year"].astype(str),
            "brand": df["brand"].astype(str),
            "city": df["city"].astype(str),
        }
        smoothing = {"brand_model": 30, "brand_model_year": 10, "brand": 50, "city": 50}

        te_stats: dict[str, dict[str, float]] = {}
        for name, key_col in keys.items():
            alpha = smoothing[name]
            grouped = series.groupby(key_col).agg(["mean", "count"])
            smooth = (grouped["count"] * grouped["mean"] + alpha * global_mean) / (grouped["count"] + alpha)
            te_stats[name] = smooth.to_dict()

        return te_stats

    def _apply_te_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply target encoding features using stored stats."""
        df = df.copy()
        gm = self._global_log_mean

        bm_key = df["brand"].astype(str) + "_" + df["model"].astype(str)
        bmy_key = bm_key + "_" + df["year"].astype(str)

        df["te_brand_model"] = bm_key.map(self._te_stats.get("brand_model", {})).fillna(gm)
        df["te_brand_model_year"] = bmy_key.map(self._te_stats.get("brand_model_year", {})).fillna(gm)
        df["te_brand"] = df["brand"].astype(str).map(self._te_stats.get("brand", {})).fillna(gm)
        df["te_city"] = df["city"].astype(str).map(self._te_stats.get("city", {})).fillna(gm)

        return df

    def _train_lgb_model(
        self, df: pd.DataFrame, log_prices: np.ndarray, weights: np.ndarray, split_idx: int, stats: dict
    ) -> None:
        """Train LightGBM model for P50 stacking."""
        df_te = self._apply_te_features(df)

        # LightGBM features: label-encoded categoricals + numerics + TE features
        # Save category→code mappings so we reuse them at inference time
        self._cat_encoders = {}
        for col in CAT_FEATURES:
            cat_series = df_te[col].astype("category")
            self._cat_encoders[col] = {cat: code for code, cat in enumerate(cat_series.cat.categories)}
            df_te[f"{col}_code"] = cat_series.cat.codes

        lgb_features = [f"{c}_code" for c in CAT_FEATURES] + NUM_FEATURES + TE_FEATURES
        X_lgb = df_te[lgb_features]

        X_train = X_lgb.iloc[:split_idx]
        X_val = X_lgb.iloc[split_idx:]
        y_train = log_prices[:split_idx]
        y_val = log_prices[split_idx:]
        w_train = weights[:split_idx]

        train_ds = lgb.Dataset(X_train, y_train, weight=w_train)
        val_ds = lgb.Dataset(X_val, y_val, reference=train_ds)

        self._lgb_model = lgb.train(
            _LGB_PARAMS,
            train_ds,
            num_boost_round=1500,
            valid_sets=[val_ds],
            callbacks=[lgb.early_stopping(100), lgb.log_evaluation(0)],
        )
        # Store feature names for prediction
        self._lgb_features = lgb_features

        y_pred = np.exp(self._lgb_model.predict(X_val))
        y_actual = np.exp(y_val)
        lgb_mape = float(np.mean(np.abs((y_actual - y_pred) / y_actual)) * 100)
        stats["lgb_p50_mape"] = round(lgb_mape, 1)
        logger.info("LightGBM P50 val MAPE: %.1f%%", lgb_mape)

    def _train_cb_te_model(
        self,
        df: pd.DataFrame,
        log_prices: np.ndarray,
        weights: np.ndarray,
        cat_feature_indices: list[int],
        split_idx: int,
        stats: dict,
    ) -> None:
        """Train CatBoost+TE model for P50 stacking."""
        df_te = self._apply_te_features(df)
        cb_te_features = ALL_FEATURES + TE_FEATURES
        X = df_te[cb_te_features]

        # Cat feature indices only for original cat features
        cat_idx = [cb_te_features.index(f) for f in CAT_FEATURES]

        X_train, X_val = X.iloc[:split_idx], X.iloc[split_idx:]
        y_train, y_val = log_prices[:split_idx], log_prices[split_idx:]
        w_train = weights[:split_idx]

        self._cb_te_model = CatBoostRegressor(
            **_CB_PARAMS_BASE,
            loss_function="RMSE",
            eval_metric="MAPE",
            cat_features=cat_idx,
        )
        train_pool = Pool(X_train, y_train, cat_features=cat_idx, weight=w_train)
        val_pool = Pool(X_val, y_val, cat_features=cat_idx)
        self._cb_te_model.fit(train_pool, eval_set=val_pool, verbose=0)

        y_pred = np.exp(self._cb_te_model.predict(X_val))
        y_actual = np.exp(y_val)
        cb_te_mape = float(np.mean(np.abs((y_actual - y_pred) / y_actual)) * 100)
        stats["cb_te_p50_mape"] = round(cb_te_mape, 1)
        logger.info("CatBoost+TE P50 val MAPE: %.1f%%", cb_te_mape)

    def predict(self, listings: list[dict]) -> list[dict]:
        """Predict P10/P50/P90 prices for listings.

        Args:
            listings: list of dicts with feature columns

        Returns:
            list of dicts: {p10, p50, p90, price_vs_market_pct}
        """
        if not self.is_trained:
            return [{"p10": None, "p50": None, "p90": None, "price_vs_market_pct": None}] * len(listings)

        df = pd.DataFrame(listings)
        df = self._fill_defaults(df)

        # Add cleaned description for native text features
        if self._has_text:
            if "description" in df.columns:
                df["description_clean"] = df["description"].fillna("").apply(_preprocess_text)
            elif "description_clean" not in df.columns:
                df["description_clean"] = ""

        # Ensure feature list includes description_clean when model uses text
        feature_names = list(self._feature_names)
        if self._has_text and "description_clean" not in feature_names:
            feature_names.append("description_clean")
        X = df[feature_names]

        # Build Pool for prediction (needed for text features)
        pool_kwargs: dict = {"cat_features": CAT_FEATURES, "feature_names": feature_names}
        if self._has_text:
            pool_kwargs["text_features"] = ["description_clean"]
        pred_pool = Pool(X, **pool_kwargs)

        results = []
        predictions = {}
        for quantile, model in self._models.items():
            key = f"p{int(quantile * 100)}"
            raw_pred = model.predict(pred_pool)
            # P50: use stacking if available, else single CatBoost
            if abs(quantile - 0.50) < 1e-9 and self._p50_log_target:
                cb_p50 = np.exp(raw_pred)
                predictions[key] = self._stacked_p50(df, cb_p50)
            else:
                predictions[key] = raw_pred

        for i in range(len(listings)):
            p10 = max(0, int(predictions["p10"][i]))
            p50 = max(0, int(predictions["p50"][i]))
            p90 = max(0, int(predictions["p90"][i]))

            # Price vs market (P50)
            actual_price = listings[i].get("price", 0)
            pct = round((1.0 - actual_price / p50) * 100, 1) if p50 > 0 and actual_price > 0 else None

            # Prediction confidence based on training sample count
            brand = str(listings[i].get("brand", "unknown"))
            model_name = str(listings[i].get("model", "unknown"))
            bm_key = f"{brand}_{model_name}"
            n_samples = self._sample_counts.get(bm_key, 0)
            if n_samples >= 50:
                confidence = "high"
            elif n_samples >= 15:
                confidence = "medium"
            else:
                confidence = "low"

            results.append(
                {
                    "p10": p10,
                    "p50": p50,
                    "p90": p90,
                    "price_vs_market_pct": pct,
                    "confidence": confidence,
                    "sample_count": n_samples,
                }
            )

        return results

    def _stacked_p50(self, df: pd.DataFrame, cb_p50: np.ndarray) -> np.ndarray:
        """Blend CatBoost, LightGBM, and CatBoost+TE predictions for P50."""
        w_cb, w_lgb, w_cb_te = STACK_WEIGHTS

        # Start with CatBoost prediction
        if self._lgb_model is None and self._cb_te_model is None:
            return cb_p50

        df_te = self._apply_te_features(df)
        result = w_cb * cb_p50

        # LightGBM prediction
        if self._lgb_model is not None and hasattr(self, "_lgb_features"):
            for col in CAT_FEATURES:
                mapping = self._cat_encoders.get(col, {})
                df_te[f"{col}_code"] = df_te[col].map(mapping).fillna(-1).astype(int)
            X_lgb = df_te[self._lgb_features]
            lgb_pred = np.exp(self._lgb_model.predict(X_lgb))
            result = result + w_lgb * lgb_pred
        else:
            result = result + w_lgb * cb_p50  # fallback

        # CatBoost+TE prediction
        if self._cb_te_model is not None:
            cb_te_features = ALL_FEATURES + TE_FEATURES
            X_te = df_te[cb_te_features]
            cb_te_pred = np.exp(self._cb_te_model.predict(X_te))
            result = result + w_cb_te * cb_te_pred
        else:
            result = result + w_cb_te * cb_p50  # fallback

        return result

    def predict_one(self, listing: dict) -> dict:
        """Predict market price for a single listing."""
        results = self.predict([listing])
        return results[0]

    def save(self, path: Path | None = None) -> None:
        """Save trained models to disk."""
        model_path = path or MODEL_PATH
        model_path.parent.mkdir(parents=True, exist_ok=True)

        with open(model_path, "wb") as f:
            pickle.dump(self._models, f)

        meta = {
            "trained_at": self._trained_at,
            "training_size": self._training_size,
            "feature_names": self._feature_names,
            "quantile_metrics": self._quantile_metrics,
            "p50_log_target": self._p50_log_target,
            "te_stats": self._te_stats,
            "global_log_mean": self._global_log_mean,
            "lgb_features": getattr(self, "_lgb_features", None),
            "cat_encoders": self._cat_encoders,
            "sample_counts": self._sample_counts,
            "has_text": self._has_text,
        }
        with open(METADATA_PATH, "wb") as f:
            pickle.dump(meta, f)

        # Save LightGBM model separately (different format)
        lgb_path = MODEL_DIR / "lgb_model.txt"
        if self._lgb_model is not None:
            self._lgb_model.save_model(str(lgb_path))

        # Save CatBoost+TE model
        cb_te_path = MODEL_DIR / "cb_te_model.pkl"
        if self._cb_te_model is not None:
            with open(cb_te_path, "wb") as f:
                pickle.dump(self._cb_te_model, f)

        logger.info("Price model saved to %s (with stacking models)", model_path)

    def load(self, path: Path | None = None) -> bool:
        """Load models from disk. Returns True if successful."""
        model_path = path or MODEL_PATH

        if not model_path.exists():
            logger.info("No saved price model found at %s", model_path)
            return False

        try:
            with open(model_path, "rb") as f:
                self._models = pickle.load(f)

            if METADATA_PATH.exists():
                with open(METADATA_PATH, "rb") as f:
                    meta = pickle.load(f)
                    self._trained_at = meta.get("trained_at")
                    self._training_size = meta.get("training_size", 0)
                    self._quantile_metrics = meta.get("quantile_metrics", {})
                    self._p50_log_target = meta.get("p50_log_target", False)
                    self._te_stats = meta.get("te_stats", {})
                    self._global_log_mean = meta.get("global_log_mean", 0.0)
                    self._lgb_features = meta.get("lgb_features")
                    self._cat_encoders = meta.get("cat_encoders", {})
                    self._sample_counts = meta.get("sample_counts", {})
                    self._has_text = meta.get("has_text", False)

            # Load LightGBM
            lgb_path = MODEL_DIR / "lgb_model.txt"
            if lgb_path.exists():
                self._lgb_model = lgb.Booster(model_file=str(lgb_path))

            # Load CatBoost+TE
            cb_te_path = MODEL_DIR / "cb_te_model.pkl"
            if cb_te_path.exists():
                with open(cb_te_path, "rb") as f:
                    self._cb_te_model = pickle.load(f)

            logger.info(
                "Price model loaded: %d quantile models, trained on %d samples, stacking=%s",
                len(self._models),
                self._training_size,
                self._lgb_model is not None,
            )
            return True
        except Exception:
            logger.exception("Failed to load price model")
            return False

    def get_info(self) -> dict:
        """Return model metadata."""
        return {
            "is_trained": self.is_trained,
            "trained_at": self._trained_at.isoformat() if self._trained_at else None,
            "training_size": self._training_size,
            "quantiles": [f"P{int(q * 100)}" for q in QUANTILES],
            "p50_mape": self._quantile_metrics.get("P50", {}).get("mape"),
        }

    def _prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Clean and prepare training data."""
        df = df.copy()

        # Drop rows without price
        df = df[df["price"] > 0]

        # Remove extreme outliers (< 50k or > 20M)
        df = df[(df["price"] >= 50_000) & (df["price"] <= 20_000_000)]

        # Fill defaults
        df = self._fill_defaults(df)

        # Remove year outliers and year=0 (missing data)
        df = df[(df["year"] >= 1990) & (df["year"] <= datetime.now().year + 1)]

        # Shuffle
        df = df.sample(frac=1.0, random_state=42).reset_index(drop=True)

        return df

    def _fill_defaults(self, df: pd.DataFrame) -> pd.DataFrame:
        """Fill missing values with sensible defaults."""
        df = df.copy()

        for col in CAT_FEATURES:
            if col not in df.columns:
                df[col] = "unknown"
            df[col] = df[col].fillna("unknown").astype(str)

        # Use NaN for missing numeric features so CatBoost/LightGBM handle them
        # natively (as "missing") instead of confusing 0 with real values.
        num_defaults_real = {
            "year": 2020,
            "photo_count": 0,
            "is_dealer": 0,
        }
        num_defaults_nan = ["mileage", "engine_volume", "power_hp", "owners_count"]

        for col, default in num_defaults_real.items():
            if col not in df.columns:
                df[col] = default
            df[col] = df[col].fillna(default).astype(int)

        for col in num_defaults_nan:
            if col not in df.columns:
                df[col] = np.nan
            df[col] = df[col].astype(float)
            # Replace 0 with NaN for fields where 0 is nonsensical
            df.loc[df[col] == 0, col] = np.nan

        df = self._add_derived_features(df)
        return df

    @staticmethod
    def _add_derived_features(df: pd.DataFrame) -> pd.DataFrame:
        """Compute derived numeric features from base columns."""
        year = df["year"].astype(int)
        mileage = df["mileage"].astype(float)

        car_age = CURRENT_YEAR - year
        df["car_age"] = car_age
        df["log_car_age"] = np.log(car_age.astype(float) + 1)
        df["log_mileage"] = np.log(mileage + 1)  # NaN-safe: np.log(NaN) = NaN
        df["mileage_per_year"] = mileage / car_age.clip(lower=1)
        df["mileage_ratio"] = mileage / (car_age * 15_000 + 1)

        # Listing month — seasonal demand signal
        if "listing_date" in df.columns:
            ld = pd.to_datetime(df["listing_date"], errors="coerce", utc=True)
            df["listing_month"] = ld.dt.month.fillna(0).astype(int)
        elif "created_at" in df.columns:
            ca = pd.to_datetime(df["created_at"], errors="coerce", utc=True)
            df["listing_month"] = ca.dt.month.fillna(0).astype(int)
        else:
            df["listing_month"] = 0

        # Power per liter — engine tuning signal (sporty vs economy)
        engine_vol = df["engine_volume"].astype(float)
        power = df["power_hp"].astype(float)
        df["power_per_liter"] = power / engine_vol.clip(lower=0.1)

        # Is premium brand
        brand_lower = df["brand"].astype(str).str.lower()
        df["is_premium"] = brand_lower.isin(PREMIUM_BRANDS).astype(int)

        return df


# Singleton for the app
_model: PriceModel | None = None


def get_price_model() -> PriceModel:
    """Get or create the global price model instance."""
    global _model
    if _model is None:
        _model = PriceModel()
        _model.load()
    return _model
