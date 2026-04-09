"""Unit tests for the Telegram bot: filter matching, listing dataclass, formatting, DemoChecker."""

from __future__ import annotations

import dataclasses

import pytest

from bot.db.models import Filter
from bot.services.checker import DemoChecker, Listing
from bot.services.notifier import _format_message, _matches

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_listing(**kwargs) -> Listing:
    defaults = dict(
        brand="Toyota",
        model="Camry",
        year=2022,
        price=1_800_000,
        market_price=2_000_000,
        discount_pct=10.0,
        category="Sedan",
        url="https://example.com/listing/100",
        photo_url=None,
    )
    defaults.update(kwargs)
    return Listing(**defaults)


def _make_filter(**kwargs) -> Filter:
    defaults = dict(
        telegram_id=1,
        brand=None,
        model=None,
        max_price=None,
        min_discount=None,
    )
    defaults.update(kwargs)
    return Filter(**defaults)


# ====================================================================
# 1. _matches() — filter matching logic
# ====================================================================


class TestMatchesBrand:
    """Brand-related matching rules."""

    def test_no_brand_filter_matches_any_brand(self):
        listing = _make_listing(brand="Kia")
        assert _matches(listing, _make_filter(brand=None)) is True

    def test_exact_brand_matches(self):
        listing = _make_listing(brand="BMW")
        assert _matches(listing, _make_filter(brand="BMW")) is True

    def test_brand_case_insensitive_upper(self):
        listing = _make_listing(brand="toyota")
        assert _matches(listing, _make_filter(brand="TOYOTA")) is True

    def test_brand_case_insensitive_mixed(self):
        listing = _make_listing(brand="Mercedes-Benz")
        assert _matches(listing, _make_filter(brand="mercedes-benz")) is True

    def test_wrong_brand_rejects(self):
        listing = _make_listing(brand="Toyota")
        assert _matches(listing, _make_filter(brand="Hyundai")) is False


class TestMatchesModel:
    """Model-related matching rules."""

    def test_no_model_filter_matches_any_model(self):
        listing = _make_listing(model="RAV4")
        assert _matches(listing, _make_filter(model=None)) is True

    def test_exact_model_matches(self):
        listing = _make_listing(model="Camry")
        assert _matches(listing, _make_filter(model="Camry")) is True

    def test_model_case_insensitive(self):
        listing = _make_listing(model="X5")
        assert _matches(listing, _make_filter(model="x5")) is True

    def test_wrong_model_rejects(self):
        listing = _make_listing(model="Camry")
        assert _matches(listing, _make_filter(model="Corolla")) is False


class TestMatchesPrice:
    """Max-price filter tests."""

    def test_no_price_filter_matches(self):
        listing = _make_listing(price=9_999_999)
        assert _matches(listing, _make_filter(max_price=None)) is True

    def test_price_under_limit(self):
        listing = _make_listing(price=1_500_000)
        assert _matches(listing, _make_filter(max_price=2_000_000)) is True

    def test_price_equal_to_limit(self):
        listing = _make_listing(price=2_000_000)
        assert _matches(listing, _make_filter(max_price=2_000_000)) is True

    def test_price_over_limit_rejects(self):
        listing = _make_listing(price=2_000_001)
        assert _matches(listing, _make_filter(max_price=2_000_000)) is False


class TestMatchesDiscount:
    """Min-discount filter tests."""

    def test_no_discount_filter_matches(self):
        listing = _make_listing(discount_pct=0.0)
        assert _matches(listing, _make_filter(min_discount=None)) is True

    def test_discount_above_threshold(self):
        listing = _make_listing(discount_pct=15.0)
        assert _matches(listing, _make_filter(min_discount=10.0)) is True

    def test_discount_equal_to_threshold(self):
        listing = _make_listing(discount_pct=10.0)
        assert _matches(listing, _make_filter(min_discount=10.0)) is True

    def test_discount_below_threshold_rejects(self):
        listing = _make_listing(discount_pct=5.0)
        assert _matches(listing, _make_filter(min_discount=10.0)) is False


class TestMatchesCombined:
    """Multiple filter fields combined."""

    def test_all_fields_match(self):
        listing = _make_listing(brand="Kia", model="K5", price=1_500_000, discount_pct=12.0)
        f = _make_filter(brand="Kia", model="K5", max_price=2_000_000, min_discount=10.0)
        assert _matches(listing, f) is True

    def test_brand_ok_but_price_too_high(self):
        listing = _make_listing(brand="Toyota", price=3_000_000)
        f = _make_filter(brand="Toyota", max_price=2_500_000)
        assert _matches(listing, f) is False

    def test_discount_ok_but_brand_wrong(self):
        listing = _make_listing(brand="BMW", discount_pct=20.0)
        f = _make_filter(brand="Toyota", min_discount=5.0)
        assert _matches(listing, f) is False

    def test_empty_filter_matches_everything(self):
        listing = _make_listing()
        f = _make_filter()
        assert _matches(listing, f) is True

    def test_brand_and_model_match_but_discount_too_low(self):
        listing = _make_listing(brand="Hyundai", model="Tucson", discount_pct=3.0)
        f = _make_filter(brand="Hyundai", model="Tucson", min_discount=5.0)
        assert _matches(listing, f) is False


# ====================================================================
# 2. DemoChecker.fetch_new() — returns valid Listing objects
# ====================================================================


class TestDemoChecker:
    @pytest.mark.asyncio
    async def test_returns_non_empty_list(self):
        checker = DemoChecker()
        listings = await checker.fetch_new()
        assert len(listings) >= 1

    @pytest.mark.asyncio
    async def test_returns_listing_instances(self):
        checker = DemoChecker()
        listings = await checker.fetch_new()
        for item in listings:
            assert isinstance(item, Listing)

    @pytest.mark.asyncio
    async def test_listing_prices_positive(self):
        checker = DemoChecker()
        listings = await checker.fetch_new()
        for item in listings:
            assert item.price > 0
            assert item.market_price > 0

    @pytest.mark.asyncio
    async def test_discount_in_valid_range(self):
        checker = DemoChecker()
        listings = await checker.fetch_new()
        for item in listings:
            assert 0 < item.discount_pct <= 100

    @pytest.mark.asyncio
    async def test_urls_are_valid(self):
        checker = DemoChecker()
        listings = await checker.fetch_new()
        for item in listings:
            assert item.url.startswith("https://")

    @pytest.mark.asyncio
    async def test_brands_from_known_set(self):
        checker = DemoChecker()
        known_brands = {"Toyota", "BMW", "Mercedes-Benz", "Kia", "Hyundai", "Volkswagen"}
        listings = await checker.fetch_new()
        for item in listings:
            assert item.brand in known_brands

    @pytest.mark.asyncio
    async def test_year_in_reasonable_range(self):
        checker = DemoChecker()
        listings = await checker.fetch_new()
        for item in listings:
            assert 2018 <= item.year <= 2025

    @pytest.mark.asyncio
    async def test_price_less_than_market_price(self):
        """Demo listings always have a discount, so price < market_price."""
        checker = DemoChecker()
        listings = await checker.fetch_new()
        for item in listings:
            assert item.price < item.market_price

    @pytest.mark.asyncio
    async def test_photo_url_is_none(self):
        """DemoChecker always sets photo_url to None."""
        checker = DemoChecker()
        listings = await checker.fetch_new()
        for item in listings:
            assert item.photo_url is None

    @pytest.mark.asyncio
    async def test_max_four_listings(self):
        """DemoChecker generates between 1 and 4 listings."""
        checker = DemoChecker()
        listings = await checker.fetch_new()
        assert 1 <= len(listings) <= 4


# ====================================================================
# 3. Listing dataclass — fields exist and are correct types
# ====================================================================


class TestListingDataclass:
    def test_is_dataclass(self):
        assert dataclasses.is_dataclass(Listing)

    def test_is_frozen(self):
        listing = _make_listing()
        with pytest.raises(dataclasses.FrozenInstanceError):
            listing.price = 999  # type: ignore[misc]

    def test_required_fields_present(self):
        fields = {f.name for f in dataclasses.fields(Listing)}
        expected = {"brand", "model", "year", "price", "market_price", "discount_pct", "category", "url", "photo_url", "deal_score", "mileage", "city", "source", "listing_id"}
        assert expected == fields

    def test_field_types(self):
        listing = _make_listing()
        assert isinstance(listing.brand, str)
        assert isinstance(listing.model, str)
        assert isinstance(listing.year, int)
        assert isinstance(listing.price, (int, float))
        assert isinstance(listing.market_price, (int, float))
        assert isinstance(listing.discount_pct, float)
        assert isinstance(listing.category, str)
        assert isinstance(listing.url, str)

    def test_photo_url_defaults_to_none(self):
        listing = Listing(
            brand="Test",
            model="Model",
            year=2023,
            price=1_000_000,
            market_price=1_200_000,
            discount_pct=16.7,
            category="Sedan",
            url="https://example.com/1",
        )
        assert listing.photo_url is None

    def test_photo_url_can_be_set(self):
        listing = _make_listing(photo_url="https://example.com/photo.jpg")
        assert listing.photo_url == "https://example.com/photo.jpg"

    def test_equality(self):
        a = _make_listing(brand="BMW", model="X5")
        b = _make_listing(brand="BMW", model="X5")
        assert a == b

    def test_inequality(self):
        a = _make_listing(brand="BMW")
        b = _make_listing(brand="Kia")
        assert a != b


# ====================================================================
# 4. _format_message() — message formatting
# ====================================================================


class TestFormatMessage:
    def test_contains_brand_and_model(self):
        listing = _make_listing(brand="Toyota", model="Camry")
        msg = _format_message(listing)
        assert "Toyota" in msg
        assert "Camry" in msg

    def test_contains_year(self):
        listing = _make_listing(year=2023)
        msg = _format_message(listing)
        assert "2023" in msg

    def test_contains_formatted_price(self):
        listing = _make_listing(price=1_800_000)
        msg = _format_message(listing)
        assert "1,800,000" in msg

    def test_contains_formatted_market_price(self):
        listing = _make_listing(market_price=2_000_000)
        msg = _format_message(listing)
        assert "2,000,000" in msg

    def test_contains_discount_percentage(self):
        listing = _make_listing(discount_pct=10.0)
        msg = _format_message(listing)
        assert "10.0%" in msg

    def test_contains_category(self):
        listing = _make_listing(category="Crossover")
        msg = _format_message(listing)
        assert "Crossover" in msg

    def test_url_in_keyboard_not_text(self):
        listing = _make_listing(url="https://auto.ru/listing/999")
        msg = _format_message(listing)
        # URL moved to inline keyboard button, not in message text
        assert "https://auto.ru/listing/999" not in msg

    def test_has_emoji_decorators(self):
        msg = _format_message(_make_listing())
        # 🚗 brand, 💰 price, 🔥 discount, category icon
        for emoji in ["\U0001f697", "\U0001f4b0", "\U0001f525"]:
            assert emoji in msg

    def test_multiline_format(self):
        msg = _format_message(_make_listing())
        lines = msg.strip().split("\n")
        # 🚗 brand, 💰 price, 🔥 discount, category icon = 4 lines minimum
        assert len(lines) >= 4

    def test_large_price_formatted_with_commas(self):
        listing = _make_listing(price=15_500_000, market_price=18_000_000)
        msg = _format_message(listing)
        assert "15,500,000" in msg
        assert "18,000,000" in msg

    def test_zero_discount(self):
        listing = _make_listing(discount_pct=0.0)
        msg = _format_message(listing)
        assert "0.0%" in msg
