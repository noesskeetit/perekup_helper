from __future__ import annotations

import pytest

from bot.services.checker import DemoChecker, Listing


@pytest.mark.asyncio
async def test_demo_checker_returns_listings():
    checker = DemoChecker()
    listings = await checker.fetch_new()
    assert len(listings) >= 1
    for item in listings:
        assert isinstance(item, Listing)
        assert item.brand
        assert item.model
        assert item.price > 0
        assert item.market_price > 0
        assert 0 < item.discount_pct <= 100
        assert item.url.startswith("https://")
