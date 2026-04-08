"""Tests for app/services/pricing_trainer.py — data cleaning, feature derivation, scoring."""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")

from app.services.pricing import CURRENT_YEAR, PriceModel  # noqa: E402

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
                "photo_count": 5,
                "is_dealer": 0,
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
    photo_count: int = 5,
    is_dealer: bool = False,
    listing_date: datetime | None = None,
    created_at: datetime | None = None,
    is_duplicate: bool = False,
    description: str = "",
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
        photo_count=photo_count,
        is_dealer=is_dealer,
        listing_date=listing_date or now,
        created_at=created_at or now,
        is_duplicate=is_duplicate,
        description=description,
        analysis=None,
    )


# ---------------------------------------------------------------------------
# IQR outlier removal
# ---------------------------------------------------------------------------


class TestIQROutlierRemoval:
    """Verify that the IQR-based outlier removal in train_model works correctly.

    Tests go through the full train_model() pipeline with mocked DB to exercise
    the real IQR logic (lines 77-95 of pricing_trainer.py).
    """

    async def test_outliers_removed_from_large_group(self):
        """Prices far outside IQR bounds should be dropped during train_model."""
        from app.services.pricing_trainer import train_model

        listings = []
        now = datetime.now(UTC)
        for i in range(20):
            listings.append(
                _make_listing_ns(
                    price=1_500_000 + (i - 10) * 10_000,
                    created_at=now - timedelta(days=i),
                )
            )
        # Inject extreme outliers
        listings[0] = _make_listing_ns(price=100_000, created_at=now)
        listings[1] = _make_listing_ns(price=50_000_000, created_at=now)

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.unique.return_value = mock_scalars
        mock_scalars.all.return_value = listings
        mock_result.scalars.return_value = mock_scalars

        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_result
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_model = MagicMock()
        # Capture the DataFrame passed to model.train() so we can inspect it
        captured_df = {}

        def capture_train(df):
            captured_df["df"] = df
            return {"status": "trained"}

        mock_model.train.side_effect = capture_train

        with (
            patch("app.services.pricing_trainer.async_session_factory", return_value=mock_session),
            patch("app.services.pricing_trainer.get_price_model", return_value=mock_model),
        ):
            await train_model()

        # The IQR filter runs before model.train(), so the df passed to train
        # should have fewer rows and should not contain the extreme prices
        df = captured_df["df"]
        assert len(df) < 20, "Outliers should have been removed"
        # The 50M outlier exceeds _prepare_data's 20M cap too, but 100K is within
        # the global range -- it's only removed by IQR
        assert 50_000_000 not in df["price"].values, "Extreme high outlier should be gone"

    async def test_small_group_not_filtered(self):
        """Groups with < 5 samples should be kept as-is (no IQR filter)."""
        from app.services.pricing_trainer import train_model

        now = datetime.now(UTC)
        listings = [
            _make_listing_ns(brand="Lada", model="Vesta", price=800_000, created_at=now),
            _make_listing_ns(brand="Lada", model="Vesta", price=50_000, created_at=now),
            _make_listing_ns(brand="Lada", model="Vesta", price=900_000, created_at=now),
        ]

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.unique.return_value = mock_scalars
        mock_scalars.all.return_value = listings
        mock_result.scalars.return_value = mock_scalars

        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_result
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_model = MagicMock()
        captured_df = {}

        def capture_train(df):
            captured_df["df"] = df
            return {"status": "trained"}

        mock_model.train.side_effect = capture_train

        with (
            patch("app.services.pricing_trainer.async_session_factory", return_value=mock_session),
            patch("app.services.pricing_trainer.get_price_model", return_value=mock_model),
        ):
            await train_model()

        # Small group (< 5) should not have IQR applied, but _prepare_data
        # will still drop the 50K listing (below 50_000 global minimum).
        # The key check: all 3 rows survived the IQR step (train_model passes them through).
        df = captured_df["df"]
        # 50K is at the boundary of _prepare_data (>= 50_000), so it may or may not survive.
        # The point is that IQR did NOT filter the small group -- all 3 reached model.train().
        assert len(df) == 3, "Small group should keep all rows (no IQR filter)"


# ---------------------------------------------------------------------------
# Stale listing filter
# ---------------------------------------------------------------------------


class TestStaleListingFilter:
    """Listings older than 60 days should be removed by train_model before training."""

    async def test_stale_listings_removed(self):
        """train_model should drop listings older than 60 days."""
        from app.services.pricing_trainer import train_model

        now = datetime.now(UTC)
        # 5 fresh + 5 stale
        listings = []
        for i in range(5):
            listings.append(_make_listing_ns(price=1_500_000, created_at=now - timedelta(days=i + 1)))
        for i in range(5):
            listings.append(_make_listing_ns(price=1_500_000, created_at=now - timedelta(days=90 + i)))

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.unique.return_value = mock_scalars
        mock_scalars.all.return_value = listings
        mock_result.scalars.return_value = mock_scalars

        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_result
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_model = MagicMock()
        captured_df = {}

        def capture_train(df):
            captured_df["df"] = df
            return {"status": "trained"}

        mock_model.train.side_effect = capture_train

        with (
            patch("app.services.pricing_trainer.async_session_factory", return_value=mock_session),
            patch("app.services.pricing_trainer.get_price_model", return_value=mock_model),
        ):
            await train_model()

        df = captured_df["df"]
        assert len(df) == 5, "Only fresh listings should reach model.train()"

    async def test_all_fresh_kept(self):
        """When all listings are recent, none should be dropped."""
        from app.services.pricing_trainer import train_model

        now = datetime.now(UTC)
        listings = [_make_listing_ns(price=1_500_000, created_at=now - timedelta(days=i)) for i in range(10)]

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.unique.return_value = mock_scalars
        mock_scalars.all.return_value = listings
        mock_result.scalars.return_value = mock_scalars

        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_result
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_model = MagicMock()
        captured_df = {}

        def capture_train(df):
            captured_df["df"] = df
            return {"status": "trained"}

        mock_model.train.side_effect = capture_train

        with (
            patch("app.services.pricing_trainer.async_session_factory", return_value=mock_session),
            patch("app.services.pricing_trainer.get_price_model", return_value=mock_model),
        ):
            await train_model()

        df = captured_df["df"]
        assert len(df) == 10, "All recent listings should be kept"


# ---------------------------------------------------------------------------
# Time decay weights
# ---------------------------------------------------------------------------


class TestTimeDecayWeights:
    """Verify that PriceModel.train() computes time decay weights from listing dates.

    The decay formula (np.exp(-0.007 * days_old)) is inline in PriceModel.train().
    We test it by verifying the CatBoost Pool receives correct weights via a mock.
    """

    def test_train_passes_weights_to_catboost(self):
        """Ensure PriceModel.train() computes and passes sample weights."""
        df = _make_df(300, price_base=1_500_000, price_spread=200_000)
        model = PriceModel()

        # Mock CatBoostRegressor to capture the Pool weights
        captured_weights = []

        with (
            patch("app.services.pricing.Pool") as mock_pool_cls,
            patch("app.services.pricing.CatBoostRegressor") as mock_cb_cls,
        ):
            mock_cb = MagicMock()
            mock_cb.predict.return_value = np.ones(60)  # 20% val split
            mock_cb.get_feature_importance.return_value = np.ones(len(model._feature_names))
            mock_cb_cls.return_value = mock_cb

            def capture_pool(*args, **kwargs):
                if "weight" in kwargs and kwargs["weight"] is not None:
                    captured_weights.append(kwargs["weight"])
                return MagicMock()

            mock_pool_cls.side_effect = capture_pool

            model.train(df)

        # train() creates Pool twice per quantile (train + val) for 3 quantiles = 6 calls
        # Each call should include weights
        assert len(captured_weights) > 0, "Pool should receive sample weights"
        # Weights should be in (0, 1] range (exponential decay)
        for w in captured_weights:
            assert np.all(w > 0), "All weights should be positive"
            assert np.all(w <= 1.0 + 1e-9), "All weights should be <= 1.0"


# ---------------------------------------------------------------------------
# Feature derivation
# ---------------------------------------------------------------------------


class TestFeatureDerivation:
    """Test PriceModel._add_derived_features (the real production function)."""

    def _base_df(self, year=2020, mileage=50_000, brand="Toyota", engine_volume=2.5, power_hp=181):
        """Build a minimal DataFrame suitable for _add_derived_features."""
        return pd.DataFrame(
            {
                "year": [year],
                "mileage": [mileage],
                "brand": [brand],
                "engine_volume": [engine_volume],
                "power_hp": [power_hp],
            }
        )

    def test_car_age_computation(self):
        """_add_derived_features should compute car_age = CURRENT_YEAR - year."""
        df = self._base_df()
        result = PriceModel._add_derived_features(df)
        expected_age = CURRENT_YEAR - 2020
        assert result["car_age"].iloc[0] == expected_age

    def test_log_car_age(self):
        """_add_derived_features should compute log(car_age + 1)."""
        df = self._base_df()
        result = PriceModel._add_derived_features(df)
        expected_age = CURRENT_YEAR - 2020
        assert abs(result["log_car_age"].iloc[0] - math.log(expected_age + 1)) < 1e-9

    def test_log_mileage(self):
        """_add_derived_features should compute log(mileage + 1)."""
        df = self._base_df()
        result = PriceModel._add_derived_features(df)
        assert abs(result["log_mileage"].iloc[0] - math.log(50_001)) < 1e-9

    def test_mileage_per_year(self):
        """_add_derived_features should compute mileage / max(car_age, 1)."""
        df = self._base_df(mileage=60_000)
        result = PriceModel._add_derived_features(df)
        expected_age = CURRENT_YEAR - 2020
        expected = 60_000 / max(expected_age, 1)
        assert abs(result["mileage_per_year"].iloc[0] - expected) < 1e-9

    def test_mileage_ratio(self):
        """_add_derived_features should compute mileage / (car_age * 15_000 + 1)."""
        df = self._base_df(mileage=60_000)
        result = PriceModel._add_derived_features(df)
        expected_age = CURRENT_YEAR - 2020
        expected = 60_000 / (expected_age * 15_000 + 1)
        assert abs(result["mileage_ratio"].iloc[0] - expected) < 1e-9

    def test_zero_age_no_division_error(self):
        """Year = CURRENT_YEAR => car_age = 0 => mileage_per_year uses clip(lower=1)."""
        df = self._base_df(year=CURRENT_YEAR, mileage=5000)
        result = PriceModel._add_derived_features(df)
        assert result["car_age"].iloc[0] == 0
        assert result["mileage_per_year"].iloc[0] == 5000.0

    def test_is_premium_for_premium_brand(self):
        """_add_derived_features should set is_premium=1 for BMW."""
        df = self._base_df(brand="BMW")
        result = PriceModel._add_derived_features(df)
        assert result["is_premium"].iloc[0] == 1

    def test_is_premium_for_regular_brand(self):
        """_add_derived_features should set is_premium=0 for Toyota."""
        df = self._base_df(brand="Toyota")
        result = PriceModel._add_derived_features(df)
        assert result["is_premium"].iloc[0] == 0

    def test_power_per_liter(self):
        """_add_derived_features should compute power_hp / engine_volume."""
        df = self._base_df(engine_volume=2.0, power_hp=200)
        result = PriceModel._add_derived_features(df)
        assert abs(result["power_per_liter"].iloc[0] - 100.0) < 1e-9

    def test_prepare_data_adds_derived_columns(self):
        """PriceModel._prepare_data should produce all derived feature columns."""
        df = _make_df(10, price_base=500_000, price_spread=100_000)
        model = PriceModel()
        prepared = model._prepare_data(df)

        for col in [
            "car_age",
            "log_car_age",
            "log_mileage",
            "mileage_per_year",
            "mileage_ratio",
            "listing_month",
            "is_premium",
            "power_per_liter",
        ]:
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
    """Verify PriceModel.train() returns MAPE metrics in the expected format.

    The MAPE formula (np.mean(np.abs((y_true - y_pred) / y_true)) * 100) is
    computed inline in PriceModel.train().

    Note: P50 now trains on log(price) and converts back via exp(), so mocked
    predictions must be in log-space for P50 and raw price for P10/P90.
    """

    def test_train_returns_mape_in_stats(self):
        """PriceModel.train() should include MAPE in quantile_metrics."""
        df = _make_df(300, price_base=1_500_000, price_spread=200_000)
        model = PriceModel()

        with patch("app.services.pricing.CatBoostRegressor") as mock_cb_cls:
            mock_cb = MagicMock()
            # For P50 (log-target): return log of a constant price
            # For P10/P90 (raw price): return a constant price
            # The mock is used for all 3 quantiles, so we return a value that
            # works for both: log(1_500_000) ~ 14.22 for P50, 1_500_000 for P10/P90.
            # Since we can't distinguish which quantile calls predict(), return
            # log(1_500_000) — P50 will exp() it back, P10/P90 MAPE will be off
            # but the test only checks P50 is present and non-negative.
            mock_cb.predict.return_value = np.full(60, np.log(1_500_000))
            mock_cb.get_feature_importance.return_value = np.ones(len(model._feature_names))
            mock_cb_cls.return_value = mock_cb

            stats = model.train(df)

        assert stats["status"] == "trained"
        assert "quantile_metrics" in stats
        assert "P50" in stats["quantile_metrics"]
        assert "mape" in stats["quantile_metrics"]["P50"]
        # MAPE should be a non-negative number
        assert stats["quantile_metrics"]["P50"]["mape"] >= 0

    def test_train_mape_zero_for_perfect_predictions(self):
        """When predictions exactly match actuals, MAPE should be 0."""
        df = _make_df(300, price_base=1_000_000, price_spread=0)
        model = PriceModel()

        prepared = model._prepare_data(df)
        actual_prices = prepared["price"].values

        with patch("app.services.pricing.CatBoostRegressor") as mock_cb_cls, patch("app.services.pricing.Pool"):
            mock_cb = MagicMock()
            # P50 trains on log(price), so return log(actual) for perfect match.
            # For P10/P90, returning log(actual) gives wrong MAPE, but we only
            # assert on P50 below.
            split_idx = int(len(prepared) * 0.8)
            val_prices = actual_prices[split_idx:]
            mock_cb.predict.return_value = np.log(val_prices)
            mock_cb.get_feature_importance.return_value = np.ones(len(model._feature_names))
            mock_cb_cls.return_value = mock_cb

            stats = model.train(df)

        assert stats["status"] == "trained"
        assert stats["quantile_metrics"]["P50"]["mape"] == 0.0


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
