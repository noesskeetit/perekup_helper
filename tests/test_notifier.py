from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from bot.db.models import Filter, NotificationLog, User
from bot.services.checker import Listing
from bot.services.notifier import _format_message, _matches, _notify_user


@pytest.fixture
def sample_listing():
    return Listing(
        brand="Toyota",
        model="Camry",
        year=2022,
        price=1_800_000,
        market_price=2_000_000,
        discount_pct=10.0,
        category="Седан",
        url="https://example.com/listing/123",
        photo_url=None,
    )


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


class TestMatches:
    def test_empty_filter_matches_everything(self, sample_listing):
        f = _make_filter()
        assert _matches(sample_listing, f) is True

    def test_brand_match(self, sample_listing):
        f = _make_filter(brand="Toyota")
        assert _matches(sample_listing, f) is True

    def test_brand_mismatch(self, sample_listing):
        f = _make_filter(brand="BMW")
        assert _matches(sample_listing, f) is False

    def test_brand_case_insensitive(self, sample_listing):
        f = _make_filter(brand="toyota")
        assert _matches(sample_listing, f) is True

    def test_model_match(self, sample_listing):
        f = _make_filter(model="Camry")
        assert _matches(sample_listing, f) is True

    def test_model_mismatch(self, sample_listing):
        f = _make_filter(model="RAV4")
        assert _matches(sample_listing, f) is False

    def test_max_price_ok(self, sample_listing):
        f = _make_filter(max_price=2_000_000)
        assert _matches(sample_listing, f) is True

    def test_max_price_exceeded(self, sample_listing):
        f = _make_filter(max_price=1_500_000)
        assert _matches(sample_listing, f) is False

    def test_min_discount_ok(self, sample_listing):
        f = _make_filter(min_discount=5.0)
        assert _matches(sample_listing, f) is True

    def test_min_discount_too_high(self, sample_listing):
        f = _make_filter(min_discount=15.0)
        assert _matches(sample_listing, f) is False

    def test_combined_filter(self, sample_listing):
        f = _make_filter(brand="Toyota", model="Camry", max_price=2_000_000, min_discount=5.0)
        assert _matches(sample_listing, f) is True


class TestFormatMessage:
    def test_contains_key_info(self, sample_listing):
        msg = _format_message(sample_listing)
        assert "Toyota" in msg
        assert "Camry" in msg
        assert "2022" in msg
        assert "1,800,000" in msg
        assert "2,000,000" in msg
        assert "10.0%" in msg
        assert "Седан" in msg
        assert "https://example.com/listing/123" in msg


class TestNotifyUser:
    @pytest.mark.asyncio
    async def test_sends_text_when_no_photo(self, sample_listing, db_session):
        bot = AsyncMock()
        with patch("bot.services.notifier.async_session", return_value=db_session):
            await _notify_user(bot, 111, sample_listing)
        bot.send_message.assert_awaited_once()
        assert "Toyota" in bot.send_message.call_args.kwargs["text"]

    @pytest.mark.asyncio
    async def test_sends_photo_when_photo_url(self, db_session):
        listing = Listing(
            brand="BMW", model="X5", year=2023,
            price=3_000_000, market_price=3_500_000,
            discount_pct=14.3, category="Кроссовер",
            url="https://example.com/listing/456",
            photo_url="https://example.com/photo.jpg",
        )
        bot = AsyncMock()
        with patch("bot.services.notifier.async_session", return_value=db_session):
            await _notify_user(bot, 222, listing)
        bot.send_photo.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_logs_notification(self, sample_listing, db_session):
        bot = AsyncMock()
        from sqlalchemy import select

        with patch("bot.services.notifier.async_session", return_value=db_session):
            await _notify_user(bot, 333, sample_listing)

        result = await db_session.execute(
            select(NotificationLog).where(NotificationLog.telegram_id == 333)
        )
        logs = result.scalars().all()
        assert len(logs) == 1
        assert logs[0].listing_url == sample_listing.url
