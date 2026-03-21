"""Tests for the seed data generator script."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.listing import AnalysisCategory, Listing, ListingAnalysis


@pytest.fixture
async def seed_session():
    """Create an in-memory SQLite async session for seed tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
async def seeded_session(seed_session: AsyncSession):
    """Run the seed generator and return the session with data."""
    from scripts.seed import generate_seed_data

    await generate_seed_data(seed_session)
    return seed_session


class TestSeedCreatesListings:
    """CP1: seed creates >= 50 records in listings."""

    async def test_creates_at_least_50_listings(self, seeded_session: AsyncSession):
        result = await seeded_session.execute(select(func.count(Listing.id)))
        count = result.scalar()
        assert count >= 50, f"Expected >= 50 listings, got {count}"


class TestSeedSources:
    """Listings include both avito and autoru sources."""

    async def test_has_avito_source(self, seeded_session: AsyncSession):
        result = await seeded_session.execute(select(func.count(Listing.id)).where(Listing.source == "avito"))
        count = result.scalar()
        assert count > 0, "No listings with source='avito'"

    async def test_has_autoru_source(self, seeded_session: AsyncSession):
        result = await seeded_session.execute(select(func.count(Listing.id)).where(Listing.source == "autoru"))
        count = result.scalar()
        assert count > 0, "No listings with source='autoru'"


class TestSeedDuplicates:
    """Some listings are marked as duplicates."""

    async def test_has_duplicates(self, seeded_session: AsyncSession):
        result = await seeded_session.execute(select(func.count(Listing.id)).where(Listing.is_duplicate.is_(True)))
        count = result.scalar()
        assert count > 0, "No duplicate listings found"


class TestSeedPriceDiff:
    """Some listings have negative price_diff_pct (below market)."""

    async def test_has_negative_price_diff(self, seeded_session: AsyncSession):
        result = await seeded_session.execute(select(func.count(Listing.id)).where(Listing.price_diff_pct < 0))
        count = result.scalar()
        assert count > 0, "No listings with negative price_diff_pct"


class TestSeedAnalysis:
    """Every listing has a corresponding listing_analysis record."""

    async def test_every_listing_has_analysis(self, seeded_session: AsyncSession):
        listing_count = (await seeded_session.execute(select(func.count(Listing.id)))).scalar()
        analysis_count = (await seeded_session.execute(select(func.count(ListingAnalysis.id)))).scalar()
        assert analysis_count == listing_count, f"Listing count ({listing_count}) != analysis count ({analysis_count})"

    async def test_all_categories_represented(self, seeded_session: AsyncSession):
        result = await seeded_session.execute(select(ListingAnalysis.category).distinct())
        categories = {row[0] for row in result.all()}
        expected = {c.value for c in AnalysisCategory}
        assert expected.issubset(categories), f"Missing categories: {expected - categories}"


class TestSeedIdempotent:
    """Running seed twice does not duplicate data."""

    async def test_idempotent(self, seed_session: AsyncSession):
        from scripts.seed import generate_seed_data

        await generate_seed_data(seed_session)
        count_first = (await seed_session.execute(select(func.count(Listing.id)))).scalar()

        await generate_seed_data(seed_session)
        count_second = (await seed_session.execute(select(func.count(Listing.id)))).scalar()

        assert count_first == count_second, (
            f"Not idempotent: {count_first} after first run, {count_second} after second"
        )


class TestSeedDataQuality:
    """Seed data is realistic and complete."""

    async def test_listings_have_descriptions(self, seeded_session: AsyncSession):
        result = await seeded_session.execute(
            select(func.count(Listing.id)).where((Listing.description.is_(None)) | (Listing.description == ""))
        )
        empty_count = result.scalar()
        assert empty_count == 0, f"{empty_count} listings have empty descriptions"

    async def test_listings_have_photos(self, seeded_session: AsyncSession):
        result = await seeded_session.execute(select(func.count(Listing.id)).where(Listing.photos.is_(None)))
        no_photos = result.scalar()
        assert no_photos == 0, f"{no_photos} listings have no photos"

    async def test_diverse_brands(self, seeded_session: AsyncSession):
        result = await seeded_session.execute(select(Listing.brand).distinct())
        brands = {row[0] for row in result.all()}
        required = {"Lada", "Toyota", "BMW", "Kia", "Hyundai"}
        assert required.issubset(brands), f"Missing brands: {required - brands}"

    async def test_analysis_has_scores(self, seeded_session: AsyncSession):
        result = await seeded_session.execute(
            select(func.count(ListingAnalysis.id)).where(ListingAnalysis.score.is_(None))
        )
        no_score = result.scalar()
        assert no_score == 0, f"{no_score} analyses have no score"
