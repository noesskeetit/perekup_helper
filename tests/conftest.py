"""Shared test fixtures."""

import os
import uuid

# Override settings before any import of bot.config or app.config
os.environ.setdefault("BOT_TOKEN", "test-token-000:AAAAAA")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine



from bot.db.models import Base as BotBase

# ---------------------------------------------------------------------------
# Bot DB session (async, used by test_models.py, test_notifier.py, etc.)
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BotBase.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


# ---------------------------------------------------------------------------
# Async web app fixtures (test_routes.py, test_stats.py)
# ---------------------------------------------------------------------------


@pytest.fixture
async def async_client():
    """Async httpx client with pre-seeded Listing+Analysis data for dashboard tests."""
    import httpx
    from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession

    from app.db.session import get_session
    from app.main import app
    from app.models.base import Base as AsyncBase
    from app.models.listing import Listing, ListingAnalysis  # noqa: F401 — register tables in metadata

    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        await conn.run_sync(AsyncBase.metadata.create_all)

    test_session_factory = async_sessionmaker(test_engine, class_=_AsyncSession, expire_on_commit=False)

    async def _override_get_session():
        async with test_session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session

    # Seed data
    async with test_session_factory() as session:
        listings = _make_async_listings()
        for listing in listings:
            session.add(listing)
        await session.commit()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
    await test_engine.dispose()


@pytest.fixture
async def async_client_empty():
    """Async httpx client with empty DB."""
    import httpx
    from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession

    from app.db.session import get_session
    from app.main import app
    from app.models.base import Base as AsyncBase
    from app.models.listing import Listing, ListingAnalysis  # noqa: F401 — register tables in metadata

    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        await conn.run_sync(AsyncBase.metadata.create_all)

    test_session_factory = async_sessionmaker(test_engine, class_=_AsyncSession, expire_on_commit=False)

    async def _override_get_session():
        async with test_session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
    await test_engine.dispose()


@pytest.fixture
async def async_detail_client():
    """Async httpx client for detail page tests with seeded data."""
    import httpx
    from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession

    from app.db.session import get_session
    from app.main import app
    from app.models.base import Base as AsyncBase
    from app.models.listing import Listing, ListingAnalysis  # noqa: F401 — register tables in metadata

    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        await conn.run_sync(AsyncBase.metadata.create_all)

    test_session_factory = async_sessionmaker(test_engine, class_=_AsyncSession, expire_on_commit=False)

    async def _override_get_session():
        async with test_session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session

    # Seed data
    async with test_session_factory() as session:
        listings = _make_async_listings()
        for listing in listings:
            session.add(listing)
        await session.commit()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
    await test_engine.dispose()


@pytest.fixture
async def async_detail_client_empty():
    """Async httpx client for detail page tests with empty DB."""
    import httpx
    from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession

    from app.db.session import get_session
    from app.main import app
    from app.models.base import Base as AsyncBase
    from app.models.listing import Listing, ListingAnalysis  # noqa: F401 — register tables in metadata

    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        await conn.run_sync(AsyncBase.metadata.create_all)

    test_session_factory = async_sessionmaker(test_engine, class_=_AsyncSession, expire_on_commit=False)

    async def _override_get_session():
        async with test_session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
    await test_engine.dispose()


# Async listing sample data (detail_client uses listing.id as UUID)
_ASYNC_LISTING_IDS = [
    uuid.UUID("11111111-1111-1111-1111-111111111111"),
    uuid.UUID("22222222-2222-2222-2222-222222222222"),
    uuid.UUID("33333333-3333-3333-3333-333333333333"),
    uuid.UUID("44444444-4444-4444-4444-444444444444"),
    uuid.UUID("55555555-5555-5555-5555-555555555555"),
]


@pytest.fixture
def async_sample_listings():
    """Async Listing+ListingAnalysis objects for test_routes.py."""
    return _make_async_listings()


def _make_async_listings():
    from app.models.listing import AnalysisCategory, Listing, ListingAnalysis

    listings = [
        Listing(
            id=_ASYNC_LISTING_IDS[0],
            source="avito",
            external_id="12345",
            brand="Toyota",
            model="Camry",
            year=2020,
            mileage=30_000,
            price=1_500_000,
            market_price=1_700_000,
            price_diff_pct=11.8,
            description="Один владелец",
            url="https://example.com/1",
            photos=[
                "https://example.com/photo1.jpg",
                "https://example.com/photo2.jpg",
                "https://example.com/photo3.jpg",
            ],
            raw_data={
                "avito_estimate": 1_650_000,
                "price_history": [
                    {"date": "2026-04-01", "price": 1_500_000},
                    {"date": "2026-03-15", "price": 1_550_000},
                    {"date": "2026-03-01", "price": 1_600_000},
                ],
            },
        ),
        Listing(
            id=_ASYNC_LISTING_IDS[1],
            source="avito",
            external_id="12346",
            brand="Toyota",
            model="RAV4",
            year=2021,
            mileage=20_000,
            price=2_300_000,
            market_price=2_500_000,
            price_diff_pct=-8.0,
            description="Хорошее состояние",
            url="https://example.com/2",
            photos=None,
            raw_data=None,
        ),
        Listing(
            id=_ASYNC_LISTING_IDS[2],
            source="avito",
            external_id="12347",
            brand="BMW",
            model="X5",
            year=2019,
            mileage=50_000,
            price=2_800_000,
            market_price=3_000_000,
            price_diff_pct=-6.7,
            description="Битая",
            url="https://example.com/3",
            photos=None,
            raw_data=None,
        ),
        Listing(
            id=_ASYNC_LISTING_IDS[3],
            source="avito",
            external_id="12348",
            brand="Kia",
            model="K5",
            year=2022,
            mileage=10_000,
            price=1_800_000,
            market_price=2_000_000,
            price_diff_pct=-10.0,
            description="Гаражное хранение",
            url="https://example.com/4",
            photos=None,
            raw_data=None,
        ),
        Listing(
            id=_ASYNC_LISTING_IDS[4],
            source="avito",
            external_id="12349",
            brand="Hyundai",
            model="Tucson",
            year=2018,
            mileage=80_000,
            price=1_200_000,
            market_price=1_400_000,
            price_diff_pct=-14.3,
            description="Требует вложений",
            url="https://example.com/5",
            photos=None,
            raw_data=None,
        ),
    ]

    analyses = [
        ListingAnalysis(
            id=uuid.uuid4(),
            listing_id=_ASYNC_LISTING_IDS[0],
            category=AnalysisCategory.CLEAN,
            confidence=0.95,
            ai_summary="Один владелец, без ДТП",
            flags=None,
            score=75.0,
        ),
        ListingAnalysis(
            id=uuid.uuid4(),
            listing_id=_ASYNC_LISTING_IDS[1],
            category=AnalysisCategory.CLEAN,
            confidence=0.88,
            ai_summary="Хорошее состояние",
            flags=None,
            score=45.0,
        ),
        ListingAnalysis(
            id=uuid.uuid4(),
            listing_id=_ASYNC_LISTING_IDS[2],
            category=AnalysisCategory.DAMAGED_BODY,
            confidence=0.82,
            ai_summary="Повреждения кузова",
            flags=["после ДТП"],
            score=20.0,
        ),
        ListingAnalysis(
            id=uuid.uuid4(),
            listing_id=_ASYNC_LISTING_IDS[3],
            category=AnalysisCategory.CLEAN,
            confidence=0.92,
            ai_summary="Гаражное хранение",
            flags=None,
            score=60.0,
        ),
        ListingAnalysis(
            id=uuid.uuid4(),
            listing_id=_ASYNC_LISTING_IDS[4],
            category=AnalysisCategory.DAMAGED_BODY,
            confidence=0.75,
            ai_summary="Требует вложений",
            flags=["износ"],
            score=15.0,
        ),
    ]

    return listings + analyses
