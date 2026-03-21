"""Tests for DatabaseChecker — fetches real listings from the main app database."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base as AppBase
from app.models.listing import AnalysisCategory, ListingAnalysis
from app.models.listing import Listing as AppListing
from bot.services.checker import DatabaseChecker, Listing


@pytest.fixture
async def app_db():
    """In-memory async SQLite with the main app schema."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(AppBase.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory

    await engine.dispose()


def _make_listing(
    *,
    brand: str = "Toyota",
    model: str = "Camry",
    year: int = 2022,
    price: int = 1_800_000,
    market_price: int = 2_000_000,
    price_diff_pct: float = -10.0,
    is_duplicate: bool = False,
    photos: list[str] | None = None,
    created_at: datetime | None = None,
    category: AnalysisCategory = AnalysisCategory.CLEAN,
    with_analysis: bool = True,
) -> list:
    """Build AppListing + optional ListingAnalysis objects for seeding."""
    lid = uuid.uuid4()
    objs: list = [
        AppListing(
            id=lid,
            source="avito",
            external_id=uuid.uuid4().hex[:8],
            brand=brand,
            model=model,
            year=year,
            mileage=30_000,
            price=price,
            market_price=market_price,
            price_diff_pct=price_diff_pct,
            url=f"https://avito.ru/{uuid.uuid4().hex[:8]}",
            photos=photos,
            is_duplicate=is_duplicate,
            created_at=created_at or datetime.now(UTC),
        ),
    ]
    if with_analysis:
        objs.append(
            ListingAnalysis(
                id=uuid.uuid4(),
                listing_id=lid,
                category=category,
                confidence=0.9,
                ai_summary="Test summary",
            )
        )
    return objs


async def test_returns_listings_from_db(app_db):
    async with app_db() as session:
        for obj in _make_listing(photos=["https://example.com/photo.jpg"]):
            session.add(obj)
        await session.commit()

    checker = DatabaseChecker(session_factory=app_db)
    listings = await checker.fetch_new()

    assert len(listings) == 1
    listing = listings[0]
    assert isinstance(listing, Listing)
    assert listing.brand == "Toyota"
    assert listing.model == "Camry"
    assert listing.year == 2022
    assert listing.price == 1_800_000
    assert listing.market_price == 2_000_000
    assert listing.discount_pct == 10.0
    assert listing.category == "clean"
    assert listing.photo_url == "https://example.com/photo.jpg"


async def test_filter_by_brand_model(app_db):
    async with app_db() as session:
        for obj in _make_listing(brand="Toyota", model="Camry"):
            session.add(obj)
        for obj in _make_listing(brand="BMW", model="X5"):
            session.add(obj)
        await session.commit()

    checker = DatabaseChecker(session_factory=app_db)

    result = await checker.fetch_new(brand="Toyota")
    assert len(result) == 1
    assert result[0].brand == "Toyota"

    checker._last_check = None
    result = await checker.fetch_new(model="X5")
    assert len(result) == 1
    assert result[0].model == "X5"

    # Case-insensitive
    checker._last_check = None
    result = await checker.fetch_new(brand="toyota")
    assert len(result) == 1
    assert result[0].brand == "Toyota"


async def test_filter_by_max_price(app_db):
    async with app_db() as session:
        for obj in _make_listing(price=1_000_000, market_price=1_200_000):
            session.add(obj)
        for obj in _make_listing(price=3_000_000, market_price=3_500_000):
            session.add(obj)
        await session.commit()

    checker = DatabaseChecker(session_factory=app_db)
    result = await checker.fetch_new(max_price=2_000_000)

    assert len(result) == 1
    assert result[0].price == 1_000_000


async def test_filter_by_min_discount(app_db):
    async with app_db() as session:
        for obj in _make_listing(price_diff_pct=-15.0):
            session.add(obj)
        for obj in _make_listing(price_diff_pct=-5.0):
            session.add(obj)
        await session.commit()

    checker = DatabaseChecker(session_factory=app_db)
    result = await checker.fetch_new(min_discount=10.0)

    assert len(result) == 1
    assert result[0].discount_pct == 15.0


async def test_duplicates_not_returned(app_db):
    async with app_db() as session:
        for obj in _make_listing(is_duplicate=False, brand="Good"):
            session.add(obj)
        for obj in _make_listing(is_duplicate=True, brand="Dup"):
            session.add(obj)
        await session.commit()

    checker = DatabaseChecker(session_factory=app_db)
    result = await checker.fetch_new()

    assert len(result) == 1
    assert result[0].brand == "Good"


async def test_only_new_after_last_check(app_db):
    old = datetime.now(UTC) - timedelta(hours=2)
    recent = datetime.now(UTC)

    async with app_db() as session:
        for obj in _make_listing(brand="Old", created_at=old):
            session.add(obj)
        for obj in _make_listing(brand="New", created_at=recent):
            session.add(obj)
        await session.commit()

    checker = DatabaseChecker(session_factory=app_db)
    checker._last_check = datetime.now(UTC) - timedelta(hours=1)

    result = await checker.fetch_new()

    assert len(result) == 1
    assert result[0].brand == "New"
