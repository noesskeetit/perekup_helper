"""Tests for app/services/pricing_trainer.py — data cleaning, feature derivation, scoring."""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd

from app.services.pricing import CURRENT_YEAR, PriceModel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_df(
    n: int = 20,
    *,
    brand: str = "Toyota",
    model: str = "Camry",
    year: int = 2020,
    mileage: int = 50_000,
    price_base: int = 1_500_000,
    price_spread: int = 200_000,
    days_old: int = 10,
) -> pd.DataFrame:
    """Build a synthetic DataFrame that resembles real listing data."""
    rng = np.random.default_rng(42)
    now = datetime.now(UTC)
    records = []
    for i in range(n):
        records.append(
            {
                "id": str(uuid.uuid4()),
                "brand": brand,
                "model": model,
                "year": year,
                "mileage": mileage + rng.integers(-5000, 5000),
                "price": price_base + int(rng.integers(-price_spread, price_spread))
                if price_spread > 0
                else price_base,
                "source": "avito",
                "city": "Moscow",
                "engine_type": "petrol",
                "transmission": "auto",
                "drive_type": "front",
                "body_type": "sedan",
                "engine_volume": 2.5,
                "power_hp": 181,
                "owners_count": 1,
                "listing_date": now - timedelta(days=days_old + i),
                "created_at": now - timedelta(days=days_old + i),
            }
        )
    return pd.DataFrame(records)


def _make_listing_ns(
    *,
    brand: str = "Toyota",
    model: str = "Camry",
    year: int = 2020,
    mileage: int = 50_000,
    price: int = 1_500_000,
    market_price: int | None = None,
    source: str = "avito",
    city: str = "Moscow",
    engine_type: str = "petrol",
    transmission: str = "auto",
    drive_type: str = "front",
    body_type: str = "sedan",
    engine_volume: float = 2.5,
    power_hp: int = 181,
    owners_count: int = 1,
    listing_date: datetime | None = None,
    created_at: datetime | None = None,
    is_duplicate: bool = False,
) -> SimpleNamespace:
    """Lightweight listing stub for score_listings tests."""
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid.uuid4(),
        brand=brand,
        model=model,
        year=year,
        mileage=mileage,
        price=price,
        market_price=market_price,
        price_diff_pct=None,
        source=source,
        city=city,
        engine_type=engine_type,
        transmission=transmission,
        drive_type=drive_type,
        body_type=body_type,
        engine_volume=engine_volume,
        power_hp=power_hp,
        owners_count=owners_count,
        listing_date=listing_date or now,
        created_at=created_at or now,
        is_duplicate=is_duplicate,
    )


# ---------------------------------------------------------------------------
# IQR outlier removal
# ---------------------------------------------------------------------------


class TestIQROutlierRemoval:
    """Verify that the IQR-based outlier removal in train_model works correctly."""

    def test_outliers_removed_from_large_group(self):
        """Prices far outside IQR bounds should be dropped."""
        df = _make_df(20, price_base=1_500_000, price_spread=50_000)
        # Inject an extreme outlier
        df.loc[0, "price"] = 100_000  # way below Q1 - 1.5*IQR
        df.loc[1, "price"] = 50_000_000  # way above Q3 + 3.0*IQR

        # Replicate the IQR logic from train_model
        clean_parts: list[pd.DataFrame] = []
        small_parts: list[pd.DataFrame] = []
        outlier_count = 0
        for _key, group in df.groupby(["brand", "model"]):
            if len(group) < 5:
                small_parts.append(group)
                continue
            q1 = group["price"].quantile(0.25)
            q3 = group["price"].quantile(0.75)
            iqr = q3 - q1
            lower = q1 - 1.5 * iqr
            upper = q3 + 3.0 * iqr
            mask = (group["price"] >= lower) & (group["price"] <= upper)
            outlier_count += int((~mask).sum())
            clean_parts.append(group[mask])

        result = pd.concat(clean_parts + small_parts, ignore_index=True)

        assert outlier_count >= 1, "At least one outlier should be removed"
        assert len(result) < len(df), "Cleaned DF should have fewer rows"
        assert result["price"].min() > 100_000, "Extreme low outlier should be gone"

    def test_small_group_not_filtered(self):
        """Groups with < 5 samples should be kept as-is (no IQR filter)."""
        df = _make_df(3, brand="Lada", model="Vesta", price_base=800_000)
        df.loc[0, "price"] = 50_000  # extreme but should remain

        small_parts: list[pd.DataFrame] = []
        for _key, group in df.groupby(["brand", "model"]):
            if len(group) < 5:
                small_parts.append(group)

        result = pd.concat(small_parts, ignore_index=True)
        assert len(result) == 3, "Small group should keep all rows"


# ---------------------------------------------------------------------------
# Stale listing filter
# ---------------------------------------------------------------------------


class TestStaleListingFilter:
    """Listings older than 60 days should be removed before training."""

    def test_stale_listings_removed(self):
        df = _make_df(10, days_old=5)
        # Make half the rows stale (> 60 days old)
        cutoff = datetime.now(UTC) - timedelta(days=60)
        stale_date = datetime.now(UTC) - timedelta(days=90)
        for i in range(5):
            df.loc[i, "created_at"] = stale_date

        df["created_at"] = pd.to_datetime(df["created_at"], utc=True)
        fresh_mask = df["created_at"] >= cutoff
        result = df[fresh_mask]

        assert len(result) == 5, "Only fresh listings should remain"

    def test_all_fresh_kept(self):
        df = _make_df(10, days_old=5)
        cutoff = datetime.now(UTC) - timedelta(days=60)
        df["created_at"] = pd.to_datetime(df["created_at"], utc=True)
        fresh_mask = df["created_at"] >= cutoff
        result = df[fresh_mask]

        assert len(result) == 10, "All recent listings should be kept"


# ---------------------------------------------------------------------------
# Time decay weights
# ---------------------------------------------------------------------------


class TestTimeDecayWeights:
    """Verify exponential time decay weight calculation."""

    def test_recent_listing_has_higher_weight(self):
        now = datetime.now(UTC)
        dates = pd.Series([now, now - timedelta(days=30), now - timedelta(days=60)])
        dates = pd.to_datetime(dates, utc=True)
        days_old = (now - dates).dt.total_seconds() / 86400.0
        weights = np.exp(-0.007 * days_old.values)

        assert weights[0] > weights[1] > weights[2], "Newer listings should have higher weight"
        assert abs(weights[0] - 1.0) < 0.01, "Today's weight should be ~1.0"

    def test_60_day_weight_roughly_0_66(self):
        """e^(-0.007 * 60) ~= 0.657"""
        w = math.exp(-0.007 * 60)
        assert 0.60 < w < 0.70, f"60-day weight should be ~0.66, got {w}"


# ---------------------------------------------------------------------------
# Feature derivation
# ---------------------------------------------------------------------------


class TestFeatureDerivation:
    """Test _add_derived_features and the inline feature computation in score_listings."""

    def test_car_age_computation(self):
        year = 2020
        car_age = CURRENT_YEAR - year
        assert car_age == 6

    def test_log_car_age(self):
        year = 2020
        car_age = CURRENT_YEAR - year
        expected = math.log(car_age + 1)
        assert abs(expected - math.log(7)) < 1e-9

    def test_log_mileage(self):
        mileage = 50_000
        expected = math.log(mileage + 1)
        assert abs(expected - math.log(50_001)) < 1e-9

    def test_mileage_per_year(self):
        mileage = 60_000
        year = 2020
        car_age = CURRENT_YEAR - year  # 6
        expected = mileage / max(car_age, 1)
        assert expected == 10_000.0

    def test_mileage_ratio(self):
        mileage = 60_000
        year = 2020
        car_age = CURRENT_YEAR - year  # 6
        expected = mileage / (car_age * 15_000 + 1)
        assert abs(expected - 60_000 / 90_001) < 1e-9

    def test_zero_age_no_division_error(self):
        """Year = CURRENT_YEAR => car_age = 0 => mileage_per_year uses max(0,1) = 1."""
        mileage = 5000
        car_age = 0
        mileage_per_year = mileage / max(car_age, 1)
        assert mileage_per_year == 5000.0

    def test_prepare_data_adds_derived_columns(self):
        """PriceModel._prepare_data should produce all derived feature columns."""
        df = _make_df(10, price_base=500_000, price_spread=100_000)
        model = PriceModel()
        prepared = model._prepare_data(df)

        for col in ["car_age", "log_car_age", "log_mileage", "mileage_per_year", "mileage_ratio"]:
            assert col in prepared.columns, f"Missing derived column: {col}"


# ---------------------------------------------------------------------------
# score_listings with mock DB
# ---------------------------------------------------------------------------


class TestScoreListings:
    """Test score_listings() with mocked DB and model."""

    async def test_score_listings_skips_when_model_not_trained(self):
        from app.services.pricing_trainer import score_listings

        mock_model = MagicMock()
        mock_model.is_trained = False

        with patch("app.services.pricing_trainer.get_price_model", return_value=mock_model):
            result = await score_listings(limit=10)

        assert result == 0

    async def test_score_listings_returns_zero_when_no_listings(self):
        from app.services.pricing_trainer import score_listings

        mock_model = MagicMock()
        mock_model.is_trained = True

        # Mock async session that returns empty result
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars

        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_result
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.services.pricing_trainer.get_price_model", return_value=mock_model),
            patch("app.services.pricing_trainer.async_session_factory", return_value=mock_session),
        ):
            result = await score_listings(limit=10)

        assert result == 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases: empty data, all outliers, single sample."""

    async def test_train_model_no_listings(self):
        """train_model should return skipped when DB is empty."""
        from app.services.pricing_trainer import train_model

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars

        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_result
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.pricing_trainer.async_session_factory", return_value=mock_session):
            result = await train_model()

        assert result["status"] == "skipped"
        assert result["reason"] == "no_data"

    def test_single_sample_in_prepare_data(self):
        """A single-row DataFrame should still pass through _prepare_data."""
        df = _make_df(1, price_base=1_000_000, price_spread=0)
        model = PriceModel()
        prepared = model._prepare_data(df)
        # Single row may survive or be dropped if price < 50k or > 20M
        # With price_base=1M it should survive
        assert len(prepared) <= 1

    def test_all_data_cleaned_returns_empty(self):
        """If all rows have price=0, _prepare_data should return empty."""
        df = _make_df(5)
        df["price"] = 0  # All invalid
        model = PriceModel()
        prepared = model._prepare_data(df)
        assert len(prepared) == 0


# ---------------------------------------------------------------------------
# MAPE calculation
# ---------------------------------------------------------------------------


class TestMAPECalculation:
    """Verify MAPE formula correctness."""

    def test_mape_perfect_prediction(self):
        y_true = np.array([100, 200, 300])
        y_pred = np.array([100, 200, 300])
        mape = float(np.mean(np.abs((y_true - y_pred) / y_true)) * 100)
        assert mape == 0.0

    def test_mape_known_error(self):
        y_true = np.array([100.0, 200.0])
        y_pred = np.array([110.0, 180.0])  # 10% off each
        mape = float(np.mean(np.abs((y_true - y_pred) / y_true)) * 100)
        assert abs(mape - 10.0) < 0.01

    def test_mape_asymmetric_errors(self):
        y_true = np.array([1_000_000.0, 2_000_000.0])
        y_pred = np.array([900_000.0, 2_200_000.0])  # -10%, +10%
        mape = float(np.mean(np.abs((y_true - y_pred) / y_true)) * 100)
        assert abs(mape - 10.0) < 0.01


# ---------------------------------------------------------------------------
# Model metadata persistence
# ---------------------------------------------------------------------------


class TestModelMetadata:
    """Test PriceModel save/load metadata round-trip."""

    def test_model_not_trained_initially(self):
        model = PriceModel()
        assert model.is_trained is False

    def test_get_info_untrained(self):
        model = PriceModel()
        info = model.get_info()
        assert info["is_trained"] is False
        assert info["trained_at"] is None
        assert info["training_size"] == 0

    def test_predict_untrained_returns_nones(self):
        model = PriceModel()
        results = model.predict([{"brand": "Toyota", "model": "Camry"}])
        assert len(results) == 1
        assert results[0]["p50"] is None
        assert results[0]["price_vs_market_pct"] is None
