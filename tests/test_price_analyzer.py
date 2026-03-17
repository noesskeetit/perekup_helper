"""Tests for price deviation calculator."""

from avito_parser.price_analyzer import calculate_price_deviation


class TestCalculatePriceDeviation:
    def test_below_market(self):
        result = calculate_price_deviation(800000, 1000000)
        assert result == -20.0

    def test_above_market(self):
        result = calculate_price_deviation(1200000, 1000000)
        assert result == 20.0

    def test_at_market_price(self):
        result = calculate_price_deviation(1000000, 1000000)
        assert result == 0.0

    def test_none_price(self):
        assert calculate_price_deviation(None, 1000000) is None

    def test_none_market_price(self):
        assert calculate_price_deviation(1000000, None) is None

    def test_zero_market_price(self):
        assert calculate_price_deviation(1000000, 0) is None

    def test_both_none(self):
        assert calculate_price_deviation(None, None) is None

    def test_significant_discount(self):
        result = calculate_price_deviation(500000, 1000000)
        assert result == -50.0

    def test_small_deviation(self):
        result = calculate_price_deviation(990000, 1000000)
        assert result == -1.0
