"""Tests for avito_parser DB integration (upsert to listings table)."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.listing import Listing  # noqa: F401 — register table in metadata
from avito_parser.db import map_card_to_listing, upsert_listing

SAMPLE_CARD = {
    "external_id": "12345",
    "url": "https://www.avito.ru/moskva/avtomobili/toyota_camry_2020_12345",
    "title": "Toyota Camry, 2020",
    "brand": "Toyota",
    "model": "Camry",
    "year": 2020,
    "mileage_km": 45000,
    "price": 1500000,
    "market_price": 1650000,
    "price_deviation_pct": -9.09,
    "description": "Отличное состояние",
    "photo_urls": '["https://00.img.avito.st/photo1.jpg"]',
}


@pytest.fixture
async def listing_session():
    """In-memory async SQLite session with the listings table."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


class TestMapCardToListing:
    def test_required_fields(self):
        result = map_card_to_listing(SAMPLE_CARD)
        assert result["source"] == "avito"
        assert result["external_id"] == "12345"
        assert result["brand"] == "Toyota"
        assert result["model"] == "Camry"
        assert result["year"] == 2020
        assert result["price"] == 1500000
        assert result["url"] == "https://www.avito.ru/moskva/avtomobili/toyota_camry_2020_12345"

    def test_optional_fields(self):
        result = map_card_to_listing(SAMPLE_CARD)
        assert result["mileage"] == 45000
        assert result["market_price"] == 1650000
        assert result["price_diff_pct"] == -9.09
        assert result["description"] == "Отличное состояние"

    def test_photo_urls_parsed_from_json_string(self):
        result = map_card_to_listing(SAMPLE_CARD)
        assert result["photos"] == ["https://00.img.avito.st/photo1.jpg"]

    def test_photo_urls_list_passthrough(self):
        card = dict(SAMPLE_CARD, photo_urls=["https://example.com/img.jpg"])
        result = map_card_to_listing(card)
        assert result["photos"] == ["https://example.com/img.jpg"]

    def test_missing_optional_fields_are_none(self):
        minimal = {"external_id": "99", "url": "https://avito.ru/test_99"}
        result = map_card_to_listing(minimal)
        assert result["mileage"] is None
        assert result["market_price"] is None
        assert result["price_diff_pct"] is None
        assert result["photos"] is None

    def test_mileage_km_renamed_to_mileage(self):
        card = {"external_id": "1", "url": "https://avito.ru/1", "mileage_km": 30000}
        result = map_card_to_listing(card)
        assert result["mileage"] == 30000
        assert "mileage_km" not in result

    def test_price_deviation_pct_renamed(self):
        card = {"external_id": "1", "url": "https://avito.ru/1", "price_deviation_pct": -5.5}
        result = map_card_to_listing(card)
        assert result["price_diff_pct"] == -5.5
        assert "price_deviation_pct" not in result

    def test_raw_data_contains_card_fields(self):
        result = map_card_to_listing(SAMPLE_CARD)
        assert result["raw_data"]["brand"] == "Toyota"
        assert result["raw_data"]["external_id"] == "12345"


class TestUpsertListing:
    async def test_inserts_new_listing(self, listing_session):
        listing, is_new = await upsert_listing(listing_session, SAMPLE_CARD)
        await listing_session.flush()

        assert is_new is True
        assert listing.external_id == "12345"
        assert listing.source == "avito"
        assert listing.brand == "Toyota"
        assert listing.price == 1500000

    async def test_updates_existing_on_repeat_parse(self, listing_session):
        await upsert_listing(listing_session, SAMPLE_CARD)
        await listing_session.flush()

        updated_card = dict(SAMPLE_CARD, price=1400000, description="Снижена цена")
        listing, is_new = await upsert_listing(listing_session, updated_card)
        await listing_session.flush()

        assert is_new is False
        assert listing.price == 1400000
        assert listing.description == "Снижена цена"

    async def test_no_duplicate_on_repeat_parse(self, listing_session):
        await upsert_listing(listing_session, SAMPLE_CARD)
        await listing_session.flush()
        await upsert_listing(listing_session, SAMPLE_CARD)
        await listing_session.flush()

        count = await listing_session.scalar(
            select(func.count()).select_from(Listing).where(Listing.external_id == "12345")
        )
        assert count == 1

    async def test_different_external_ids_create_separate_rows(self, listing_session):
        card_a = dict(SAMPLE_CARD, external_id="111")
        card_b = dict(SAMPLE_CARD, external_id="222")

        await upsert_listing(listing_session, card_a)
        await upsert_listing(listing_session, card_b)
        await listing_session.flush()

        count = await listing_session.scalar(select(func.count()).select_from(Listing).where(Listing.source == "avito"))
        assert count == 2

    async def test_raises_without_external_id(self, listing_session):
        with pytest.raises(ValueError, match="external_id"):
            await upsert_listing(listing_session, {"brand": "Toyota"})
