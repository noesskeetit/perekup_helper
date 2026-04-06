"""Tests for validation logic in app.parsers.normalizer.normalize_listing()."""

from __future__ import annotations

from app.parsers.base import ParsedListing
from app.parsers.normalizer import normalize_listing


def _make_parsed_listing(**overrides) -> ParsedListing:
    """Build a valid ParsedListing with sane defaults, applying overrides."""
    defaults = {
        "source": "test",
        "external_id": "t-001",
        "brand": "Toyota",
        "model": "Camry",
        "year": 2020,
        "price": 1_500_000,
        "url": "https://example.com/1",
        "mileage": 50_000,
        "photos": ["https://example.com/photo1.jpg"],
    }
    defaults.update(overrides)
    return ParsedListing(**defaults)


# ── Mileage validation ──────────────────────────────────────────────────────


class TestMileageValidation:
    """Mileage > 999_999 or negative → reset to None."""

    def test_mileage_above_limit_reset_to_none(self):
        listing = _make_parsed_listing(mileage=1_000_000)
        result = normalize_listing(listing)
        assert result is not None
        assert result.mileage is None

    def test_mileage_at_limit_kept(self):
        listing = _make_parsed_listing(mileage=999_999)
        result = normalize_listing(listing)
        assert result is not None
        assert result.mileage == 999_999

    def test_negative_mileage_reset_to_none(self):
        listing = _make_parsed_listing(mileage=-1)
        result = normalize_listing(listing)
        assert result is not None
        assert result.mileage is None

    def test_normal_mileage_preserved(self):
        listing = _make_parsed_listing(mileage=80_000)
        result = normalize_listing(listing)
        assert result is not None
        assert result.mileage == 80_000

    def test_none_mileage_stays_none(self):
        listing = _make_parsed_listing(mileage=None)
        result = normalize_listing(listing)
        assert result is not None
        assert result.mileage is None


# ── Price validation ─────────────────────────────────────────────────────────


class TestPriceValidation:
    """Price < 10_000 or > 50_000_000 → listing discarded (returns None)."""

    def test_price_below_minimum_returns_none(self):
        listing = _make_parsed_listing(price=9_999)
        result = normalize_listing(listing)
        assert result is None

    def test_price_at_minimum_kept(self):
        listing = _make_parsed_listing(price=10_000)
        result = normalize_listing(listing)
        assert result is not None

    def test_price_above_maximum_returns_none(self):
        listing = _make_parsed_listing(price=50_000_001)
        result = normalize_listing(listing)
        assert result is None

    def test_price_at_maximum_kept(self):
        listing = _make_parsed_listing(price=50_000_000)
        result = normalize_listing(listing)
        assert result is not None

    def test_zero_price_returns_none(self):
        listing = _make_parsed_listing(price=0)
        result = normalize_listing(listing)
        assert result is None

    def test_normal_price_preserved(self):
        listing = _make_parsed_listing(price=1_500_000)
        result = normalize_listing(listing)
        assert result is not None


# ── Year validation ──────────────────────────────────────────────────────────


class TestYearValidation:
    """Year < 1970 or > 2027 → reset to 0."""

    def test_year_below_1970_reset_to_zero(self):
        listing = _make_parsed_listing(year=1969)
        result = normalize_listing(listing)
        assert result is not None
        assert result.year == 0

    def test_year_1970_kept(self):
        listing = _make_parsed_listing(year=1970)
        result = normalize_listing(listing)
        assert result is not None
        assert result.year == 1970

    def test_year_above_2027_reset_to_zero(self):
        listing = _make_parsed_listing(year=2028)
        result = normalize_listing(listing)
        assert result is not None
        assert result.year == 0

    def test_year_2027_kept(self):
        listing = _make_parsed_listing(year=2027)
        result = normalize_listing(listing)
        assert result is not None
        assert result.year == 2027

    def test_normal_year_preserved(self):
        listing = _make_parsed_listing(year=2020)
        result = normalize_listing(listing)
        assert result is not None
        assert result.year == 2020


# ── Derived fields ───────────────────────────────────────────────────────────


class TestDerivedFields:
    """Verify photo_count and is_dealer are derived correctly."""

    def test_photo_count_from_photos_list(self):
        listing = _make_parsed_listing(photos=["a.jpg", "b.jpg", "c.jpg"])
        result = normalize_listing(listing)
        assert result is not None
        assert result.photo_count == 3

    def test_photo_count_zero_when_no_photos(self):
        listing = _make_parsed_listing(photos=[])
        result = normalize_listing(listing)
        assert result is not None
        assert result.photo_count == 0

    def test_is_dealer_from_seller_type(self):
        listing = _make_parsed_listing(seller_type="дилер")
        result = normalize_listing(listing)
        assert result is not None
        assert result.is_dealer is True

    def test_is_not_dealer_for_private(self):
        listing = _make_parsed_listing(seller_type="частное лицо")
        result = normalize_listing(listing)
        assert result is not None
        assert result.is_dealer is False
