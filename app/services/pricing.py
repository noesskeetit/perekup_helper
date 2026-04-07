"""Market price modeling using CatBoost quantile regression.

Trains on collected listings to predict P10/P50/P90 price for any car
configuration. Used to compute market_price and price_diff_pct.

Features: brand, model, year, mileage, engine, transmission, drive, city
Target: price (from real listings)

Quantiles:
  - P10: cheap end of market (below this = hot deal)
  - P50: fair market price
  - P90: expensive end (above this = overpriced)

Model retrains daily on fresh data.

MAPE optimization notes (v2):
  - P50 model trains on log(price) to normalize relative errors across price ranges.
    Predictions are exp(log_pred) to recover original scale.
  - P10/P90 still use Quantile loss on raw price (they bound the market range).
  - CatBoost hyperparameters tuned for lower MAPE: more iterations, lower LR,
    deeper trees, L2 regularization.
  - 5-fold CV is used for reliable MAPE measurement alongside the final model.
"""

from __future__ import annotations

import logging
import math
import pickle
from datetime import UTC, datetime
from pathlib import Path

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


class PriceModel:
    """CatBoost quantile regression model for car market pricing.

    Predicts P10, P50, P90 price given car features.

    The P50 model is trained on log(price) with MAPE eval metric, then
    predictions are exponentiated back. This dramatically reduces MAPE because
    relative errors become uniform across price ranges.
    """

    def __init__(self):
        self._models: dict[float, CatBoostRegressor] = {}
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

        for quantile in QUANTILES:
            logger.info("Training quantile=%.2f on %d samples", quantile, len(df))

            is_p50 = abs(quantile - 0.50) < 1e-9

            if is_p50:
                # P50: train on log(price) with RMSE loss + MAPE eval
                y_target = np.log(y.values)
                model = CatBoostRegressor(
                    **_CB_PARAMS_BASE,
                    loss_function="RMSE",
                    eval_metric="MAPE",
                    cat_features=cat_feature_indices,
                )
                self._p50_log_target = True
            else:
                # P10/P90: quantile loss on raw price
                y_target = y.values
                model = CatBoostRegressor(
                    **_CB_PARAMS_BASE,
                    loss_function=f"Quantile:alpha={quantile}",
                    cat_features=cat_feature_indices,
                )

            # 80/20 split for early stopping
            split_idx = int(len(df) * 0.8)
            X_train, X_val = X.iloc[:split_idx], X.iloc[split_idx:]
            y_train, y_val = y_target[:split_idx], y_target[split_idx:]
            w_train, w_val = weights[:split_idx], weights[split_idx:]

            train_pool = Pool(X_train, y_train, cat_features=cat_feature_indices, weight=w_train)
            val_pool = Pool(X_val, y_val, cat_features=cat_feature_indices, weight=w_val)

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

        # --- 5-fold CV MAPE for P50 (reliable metric, does not affect the final model) ---
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
            w_val = weights[val_start:val_end]

            y_train_log = np.log(y_train_raw)
            y_val_log = np.log(y_val_raw)

            model = CatBoostRegressor(
                **_CB_PARAMS_BASE,
                loss_function="RMSE",
                eval_metric="MAPE",
                cat_features=cat_feature_indices,
            )

            train_pool = Pool(X_train, y_train_log, cat_features=cat_feature_indices, weight=w_train)
            val_pool = Pool(X_val, y_val_log, cat_features=cat_feature_indices, weight=w_val)
            model.fit(train_pool, eval_set=val_pool, verbose=0)

            y_pred = np.exp(model.predict(X_val))
            fold_mape = float(np.mean(np.abs((y_val_raw - y_pred) / y_val_raw)) * 100)
            mapes.append(fold_mape)

        return float(np.mean(mapes))

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
        X = df[self._feature_names]

        results = []
        predictions = {}
        for quantile, model in self._models.items():
            key = f"p{int(quantile * 100)}"
            raw_pred = model.predict(X)
            # P50 trained on log(price) — exponentiate back
            if abs(quantile - 0.50) < 1e-9 and self._p50_log_target:
                predictions[key] = np.exp(raw_pred)
            else:
                predictions[key] = raw_pred

        for i in range(len(listings)):
            p10 = max(0, int(predictions["p10"][i]))
            p50 = max(0, int(predictions["p50"][i]))
            p90 = max(0, int(predictions["p90"][i]))

            # Price vs market (P50)
            actual_price = listings[i].get("price", 0)
            pct = round((1.0 - actual_price / p50) * 100, 1) if p50 > 0 and actual_price > 0 else None

            results.append(
                {
                    "p10": p10,
                    "p50": p50,
                    "p90": p90,
                    "price_vs_market_pct": pct,
                }
            )

        return results

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
        }
        with open(METADATA_PATH, "wb") as f:
            pickle.dump(meta, f)

        logger.info("Price model saved to %s", model_path)

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

            logger.info(
                "Price model loaded: %d quantile models, trained on %d samples", len(self._models), self._training_size
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

        num_defaults = {
            "year": 2020,
            "mileage": 0,
            "engine_volume": 0.0,
            "power_hp": 0,
            "owners_count": 0,
            "photo_count": 0,
            "is_dealer": 0,
        }
        for col, default in num_defaults.items():
            if col not in df.columns:
                df[col] = default
            df[col] = df[col].fillna(default)
            if isinstance(default, float):
                df[col] = df[col].astype(float)
            else:
                df[col] = df[col].astype(int)

        df = self._add_derived_features(df)
        return df

    @staticmethod
    def _add_derived_features(df: pd.DataFrame) -> pd.DataFrame:
        """Compute derived numeric features from base columns."""
        year = df["year"].astype(int)
        mileage = df["mileage"].astype(float)

        car_age = CURRENT_YEAR - year
        df["car_age"] = car_age
        df["log_car_age"] = car_age.apply(lambda a: math.log(a + 1))
        df["log_mileage"] = mileage.apply(lambda m: math.log(m + 1))
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
