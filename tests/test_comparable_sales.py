"""Tests for app/services/comparable_sales.py — distance, comparables, pricing."""

from __future__ import annotations

import math
from unittest.mock import AsyncMock, patch

from app.services.comparable_sales import (
    ENGINE_NORM,
    W_ENGINE,
    W_MILEAGE,
    W_YEAR,
    YEAR_NORM,
    _normalised_distance,
    _percentile,
    compute_comparable_price,
    find_comparables,
)

# ---------------------------------------------------------------------------
# Distance calculation
# ---------------------------------------------------------------------------


class TestNormalisedDistance:
    """Verify the weighted distance function between two listings."""

    def test_identical_listings_zero_distance(self):
        dist = _normalised_distance(
            target_year=2020,
            target_mileage=50_000,
            target_engine=2.0,
            cand_year=2020,
            cand_mileage=50_000,
            cand_engine=2.0,
        )
        assert dist == 0.0

    def test_year_diff_only(self):
        """2 year diff => year_component = 0.35 * (2/2)^2 = 0.35"""
        dist = _normalised_distance(
            target_year=2020,
            target_mileage=50_000,
            target_engine=2.0,
            cand_year=2022,
            cand_mileage=50_000,
            cand_engine=2.0,
        )
        year_component = W_YEAR * (2 / YEAR_NORM) ** 2
        assert abs(dist - year_component) < 1e-9

    def test_mileage_diff_only(self):
        """Log-scale mileage diff with same year and engine."""
        t_mil = 50_000
        c_mil = 80_000
        log_diff = abs(math.log(t_mil) - math.log(c_mil))
        expected_mileage = W_MILEAGE * log_diff**2

        dist = _normalised_distance(
            target_year=2020,
            target_mileage=t_mil,
            target_engine=2.0,
            cand_year=2020,
            cand_mileage=c_mil,
            cand_engine=2.0,
        )
        assert abs(dist - expected_mileage) < 1e-9

    def test_engine_diff_only(self):
        """Engine volume diff: 0.5L => engine_component = 0.20 * (0.5/0.5)^2 = 0.20"""
        dist = _normalised_distance(
            target_year=2020,
            target_mileage=50_000,
            target_engine=2.0,
            cand_year=2020,
            cand_mileage=50_000,
            cand_engine=2.5,
        )
        engine_component = W_ENGINE * (0.5 / ENGINE_NORM) ** 2
        assert abs(dist - engine_component) < 1e-9

    def test_combined_distance_is_sum_of_components(self):
        """Distance should equal the sum of all three weighted components."""
        t_year, c_year = 2020, 2021
        t_mil, c_mil = 50_000, 60_000
        t_eng, c_eng = 2.0, 2.5

        year_comp = W_YEAR * (abs(t_year - c_year) / YEAR_NORM) ** 2
        log_mil = abs(math.log(max(t_mil, 1)) - math.log(max(c_mil, 1)))
        mil_comp = W_MILEAGE * log_mil**2
        eng_comp = W_ENGINE * (abs(t_eng - c_eng) / ENGINE_NORM) ** 2

        expected = year_comp + mil_comp + eng_comp
        dist = _normalised_distance(t_year, t_mil, t_eng, c_year, c_mil, c_eng)
        assert abs(dist - expected) < 1e-9

    def test_distance_is_non_negative(self):
        dist = _normalised_distance(2020, 100_000, 1.6, 2015, 200_000, 3.5)
        assert dist >= 0.0

    def test_zero_mileage_handled(self):
        """Zero mileage should use max(0, 1) = 1 to avoid log(0)."""
        dist = _normalised_distance(2020, 0, 2.0, 2020, 0, 2.0)
        assert dist == 0.0


# ---------------------------------------------------------------------------
# Finding comparables — filters (brand, model, year +/-2, mileage +/-30K)
# ---------------------------------------------------------------------------


class TestFindComparables:
    """Test find_comparables with mocked DB query."""

    async def test_returns_sorted_by_distance(self):
        """Comparables should be sorted by distance ascending."""
        mock_candidates = [
            {"price": 1_500_000, "year": 2020, "mileage": 55_000, "engine_volume": 2.0, "source": "avito"},
            {"price": 1_400_000, "year": 2019, "mileage": 70_000, "engine_volume": 2.0, "source": "autoru"},
            {"price": 1_600_000, "year": 2020, "mileage": 50_000, "engine_volume": 2.0, "source": "drom"},
        ]

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.services.comparable_sales.async_session_factory", return_value=mock_session),
            patch(
                "app.services.comparable_sales._query_candidates", new_callable=AsyncMock, return_value=mock_candidates
            ),
        ):
            result = await find_comparables(
                {"brand": "Toyota", "model": "Camry", "year": 2020, "mileage": 50_000, "engine_volume": 2.0},
                k=10,
            )

        assert len(result) == 3
        distances = [r["distance"] for r in result]
        assert distances == sorted(distances), "Results should be sorted by distance"

    async def test_respects_k_limit(self):
        """Only top-k comparables should be returned."""
        mock_candidates = [
            {
                "price": 1_000_000 + i * 100_000,
                "year": 2020,
                "mileage": 50_000 + i * 1000,
                "engine_volume": 2.0,
                "source": "avito",
            }
            for i in range(20)
        ]

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.services.comparable_sales.async_session_factory", return_value=mock_session),
            patch(
                "app.services.comparable_sales._query_candidates", new_callable=AsyncMock, return_value=mock_candidates
            ),
        ):
            result = await find_comparables(
                {"brand": "Toyota", "model": "Camry", "year": 2020, "mileage": 50_000, "engine_volume": 2.0},
                k=5,
            )

        assert len(result) == 5

    async def test_no_candidates_returns_empty(self):
        """When no candidates match filters, return empty list."""
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.services.comparable_sales.async_session_factory", return_value=mock_session),
            patch("app.services.comparable_sales._query_candidates", new_callable=AsyncMock, return_value=[]),
        ):
            result = await find_comparables(
                {"brand": "RareCarBrand", "model": "UniqueModel", "year": 2020},
                k=10,
            )

        assert result == []


# ---------------------------------------------------------------------------
# compute_comparable_price — weighted average / percentiles
# ---------------------------------------------------------------------------


class TestComputeComparablePrice:
    """Test pricing statistics from a set of comparable listings."""

    def test_median_of_odd_count(self):
        comparables = [
            {"price": 1_000_000},
            {"price": 1_200_000},
            {"price": 1_500_000},
        ]
        result = compute_comparable_price(comparables)
        assert result["median_price"] == 1_200_000
        assert result["count"] == 3

    def test_median_of_even_count(self):
        comparables = [
            {"price": 1_000_000},
            {"price": 1_200_000},
            {"price": 1_400_000},
            {"price": 1_600_000},
        ]
        result = compute_comparable_price(comparables)
        # P50 with linear interpolation of sorted [1M, 1.2M, 1.4M, 1.6M]
        # k = 0.5 * 3 = 1.5, lo=1, hi=2, frac=0.5
        # P50 = 1_200_000 + 0.5 * 200_000 = 1_300_000
        assert result["median_price"] == 1_300_000

    def test_p25_and_p75(self):
        comparables = [{"price": p} for p in [100, 200, 300, 400, 500]]
        result = compute_comparable_price(comparables)
        assert result["p25_price"] is not None
        assert result["p75_price"] is not None
        assert result["p25_price"] <= result["median_price"] <= result["p75_price"]

    def test_single_comparable(self):
        comparables = [{"price": 1_500_000}]
        result = compute_comparable_price(comparables)
        assert result["median_price"] == 1_500_000
        assert result["p25_price"] == 1_500_000
        assert result["p75_price"] == 1_500_000
        assert result["count"] == 1

    def test_no_comparables(self):
        result = compute_comparable_price([])
        assert result["median_price"] is None
        assert result["count"] == 0
        assert result["confidence"] == 0.0


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------


class TestConfidenceScoring:
    """Confidence scales from 0.2 (1 comparable) to 1.0 (>= 10)."""

    def test_confidence_one_comparable(self):
        result = compute_comparable_price([{"price": 1_000_000}])
        assert result["confidence"] == 0.2

    def test_confidence_ten_comparables(self):
        comparables = [{"price": 1_000_000 + i * 50_000} for i in range(10)]
        result = compute_comparable_price(comparables)
        assert result["confidence"] == 1.0

    def test_confidence_twenty_comparables(self):
        """More than 10 should still be 1.0."""
        comparables = [{"price": 1_000_000 + i * 50_000} for i in range(20)]
        result = compute_comparable_price(comparables)
        assert result["confidence"] == 1.0

    def test_confidence_five_comparables(self):
        """5 comparables: 0.2 + 0.8 * 4/9 = 0.2 + 0.356 = 0.56"""
        comparables = [{"price": 1_000_000 + i * 50_000} for i in range(5)]
        result = compute_comparable_price(comparables)
        expected = round(0.2 + 0.8 * 4 / 9, 2)
        assert result["confidence"] == expected

    def test_confidence_monotonically_increases(self):
        """Adding more comparables should never decrease confidence."""
        prev_conf = 0.0
        for n in range(1, 15):
            comparables = [{"price": 1_000_000 + i * 50_000} for i in range(n)]
            conf = compute_comparable_price(comparables)["confidence"]
            assert conf >= prev_conf, f"Confidence dropped at n={n}"
            prev_conf = conf


# ---------------------------------------------------------------------------
# _percentile helper
# ---------------------------------------------------------------------------


class TestPercentileHelper:
    """Test the internal _percentile function."""

    def test_single_value(self):
        assert _percentile([100], 50) == 100.0

    def test_median_of_three(self):
        assert _percentile([10, 20, 30], 50) == 20.0

    def test_p0_returns_minimum(self):
        assert _percentile([10, 20, 30], 0) == 10.0

    def test_p100_returns_maximum(self):
        assert _percentile([10, 20, 30], 100) == 30.0

    def test_interpolation_p25(self):
        """p25 of [10, 20, 30, 40] => k=0.75, lo=0, hi=1 => 10 + 0.75*10 = 17.5"""
        result = _percentile([10, 20, 30, 40], 25)
        assert abs(result - 17.5) < 1e-9
