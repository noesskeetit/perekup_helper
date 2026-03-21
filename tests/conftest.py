"""Shared test fixtures."""

import os
import uuid

# Override settings before any import of bot.config or app.config
os.environ.setdefault("BOT_TOKEN", "test-token-000:AAAAAA")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

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
# Sync REST-API app fixtures (test_listings.py, test_stats.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def sync_client(sample_listings):
    """Sync TestClient with pre-populated listings for API router tests."""

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.database import Base, get_db

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)

    def _override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()

    from app.routers.listings import router as listings_router
    from app.routers.stats import router as stats_router

    app.include_router(listings_router)
    app.include_router(stats_router)
    app.dependency_overrides[get_db] = _override_get_db

    # Seed data
    db = TestSession()
    for listing in sample_listings:
        db.add(listing)
    db.commit()
    db.close()

    return TestClient(app)


@pytest.fixture
def sync_client_empty():
    """Sync TestClient with empty DB for tests that expect no data."""

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.database import Base, get_db
    from app.models.sync_listing import SyncListing  # noqa: F401 — ensure table is registered in Base.metadata

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def _override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()

    from app.routers.listings import router as listings_router
    from app.routers.stats import router as stats_router

    app.include_router(listings_router)
    app.include_router(stats_router)
    app.dependency_overrides[get_db] = _override_get_db

    return TestClient(app)


@pytest.fixture
def sample_listings():
    """Sample SyncListing objects for sync API tests."""
    from datetime import datetime

    from app.models.sync_listing import SyncListing

    now = datetime.utcnow()
    return [
        SyncListing(
            id=1,
            source="avito",
            brand="Toyota",
            model="Camry",
            year=2020,
            price=1_500_000,
            mileage=30_000,
            market_price=1_700_000,
            price_diff=-200_000,
            market_diff_pct=-11.8,
            score=8.5,
            category="clean",
            ai_analysis="Один владелец, без ДТП, один хозяин",
            source_url="https://example.com/1",
            image_url=None,
            created_at=now,
            updated_at=now,
        ),
        SyncListing(
            id=2,
            source="avito",
            brand="Toyota",
            model="RAV4",
            year=2021,
            price=2_300_000,
            mileage=20_000,
            market_price=2_500_000,
            price_diff=-200_000,
            market_diff_pct=-8.0,
            score=7.0,
            category="clean",
            ai_analysis="Хорошее состояние",
            source_url="https://example.com/2",
            image_url=None,
            created_at=now,
            updated_at=now,
        ),
        SyncListing(
            id=3,
            source="autoru",
            brand="BMW",
            model="X5",
            year=2019,
            price=2_800_000,
            mileage=50_000,
            market_price=3_000_000,
            price_diff=-200_000,
            market_diff_pct=-6.7,
            score=6.5,
            category="damaged_body",
            ai_analysis="Незначительные повреждения кузова",
            source_url="https://example.com/3",
            image_url=None,
            created_at=now,
            updated_at=now,
        ),
        SyncListing(
            id=4,
            source="autoru",
            brand="Kia",
            model="K5",
            year=2022,
            price=1_800_000,
            mileage=10_000,
            market_price=2_000_000,
            price_diff=-200_000,
            market_diff_pct=-10.0,
            score=9.0,
            category="clean",
            ai_analysis="Отличное состояние, гаражное хранение",
            source_url="https://example.com/4",
            image_url=None,
            created_at=now,
            updated_at=now,
        ),
        SyncListing(
            id=5,
            source="avito",
            brand="Hyundai",
            model="Tucson",
            year=2018,
            price=1_200_000,
            mileage=80_000,
            market_price=1_400_000,
            price_diff=-200_000,
            market_diff_pct=-14.3,
            score=4.0,
            category="damaged_body",
            ai_analysis="Требует вложений",
            source_url="https://example.com/5",
            image_url=None,
            created_at=now,
            updated_at=now,
        ),
    ]


# ---------------------------------------------------------------------------
# Async web app fixtures (test_routes.py)
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
            price_diff_pct=-11.8,
            description="Один владелец",
            url="https://example.com/1",
            photos=["https://example.com/photo1.jpg", "https://example.com/photo2.jpg"],
            raw_data=None,
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
        ),
        ListingAnalysis(
            id=uuid.uuid4(),
            listing_id=_ASYNC_LISTING_IDS[1],
            category=AnalysisCategory.CLEAN,
            confidence=0.88,
            ai_summary="Хорошее состояние",
            flags=None,
        ),
        ListingAnalysis(
            id=uuid.uuid4(),
            listing_id=_ASYNC_LISTING_IDS[2],
            category=AnalysisCategory.DAMAGED_BODY,
            confidence=0.82,
            ai_summary="Повреждения кузова",
            flags=["после ДТП"],
        ),
        ListingAnalysis(
            id=uuid.uuid4(),
            listing_id=_ASYNC_LISTING_IDS[3],
            category=AnalysisCategory.CLEAN,
            confidence=0.92,
            ai_summary="Гаражное хранение",
            flags=None,
        ),
        ListingAnalysis(
            id=uuid.uuid4(),
            listing_id=_ASYNC_LISTING_IDS[4],
            category=AnalysisCategory.DAMAGED_BODY,
            confidence=0.75,
            ai_summary="Требует вложений",
            flags=["износ"],
        ),
    ]

    return listings + analyses


# ---------------------------------------------------------------------------
# Avito parser fixtures (test_card_parser.py, test_listing_parser.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_card_html():
    """Mock Avito card page HTML with JSON-LD and parameter elements."""
    return """<!DOCTYPE html>
<html>
<head>
<script type="application/ld+json">
{
    "@type": "Product",
    "name": "Toyota Camry, 2020",
    "description": "Отличное состояние, один владелец, гаражное хранение",
    "offers": {"price": "1500000"},
    "vehicleIdentificationNumber": "JTDBR40E600123456",
    "image": ["https://00.img.avito.st/image/1/test_photo.jpg"]
}
</script>
<script type="application/json">
{
    "item": {
        "id": 12345,
        "title": "Toyota Camry, 2020",
        "description": "Отличное состояние",
        "price": 1500000,
        "location": {"name": "Москва, Центральный район"}
    },
    "params": [
        {"title": "Марка", "value": "Toyota"},
        {"title": "Модель", "value": "Camry"},
        {"title": "Год выпуска", "value": "2020"},
        {"title": "Пробег", "value": "45 000 км"},
        {"title": "Тип двигателя", "value": "Бензин"},
        {"title": "Объём двигателя", "value": "2.5 л"},
        {"title": "Мощность", "value": "200 л.с."},
        {"title": "Коробка передач", "value": "Автомат"},
        {"title": "Привод", "value": "Передний"},
        {"title": "Тип кузова", "value": "Седан"},
        {"title": "Цвет", "value": "Белый"},
        {"title": "Руль", "value": "Левый"}
    ]
}
</script>
</head>
<body>
<div data-marker="item-view/title-info"><h1>Toyota Camry, 2020</h1></div>
<span data-marker="item-view/item-price" content="1500000">1 500 000 ₽</span>
<div data-marker="item-view/item-description">Отличное состояние, один владелец</div>
<div data-marker="seller-info/name">Алексей</div>
<span data-marker="item-view/item-address">Москва, Центральный район</span>
<script>var config = {"marketPrice": 1650000};</script>
</body>
</html>"""


@pytest.fixture
def mock_card_html_embedded_json():
    """Mock card page with embedded JSON state but no JSON-LD."""
    return """<!DOCTYPE html>
<html>
<head>
<script type="application/json">
{
    "item": {
        "id": 12345,
        "title": "BMW 3 Series, 2019",
        "description": "Хорошее состояние",
        "price": 2300000,
        "location": {"name": "Санкт-Петербург"}
    },
    "params": [
        {"title": "Марка", "value": "BMW"},
        {"title": "Модель", "value": "3 Series"},
        {"title": "Год выпуска", "value": "2019"},
        {"title": "Пробег", "value": "67 000 км"}
    ]
}
</script>
</head>
<body>
<h1>BMW 3 Series, 2019</h1>
<script>var data = {"vin": "WBAPH5C55BA123456"};</script>
</body>
</html>"""


@pytest.fixture
def mock_listing_html():
    """Mock Avito listing page HTML with multiple ads and pagination."""
    return """<!DOCTYPE html>
<html>
<body>
<div data-marker="item" itemtype="http://schema.org/Product">
    <a data-marker="item-title" href="/moskva/avtomobili/toyota_camry_2020_12345">
        Toyota Camry 2020
    </a>
    <meta itemprop="price" content="1500000">
</div>
<div data-marker="item" itemtype="http://schema.org/Product">
    <a data-marker="item-title" href="/moskva/avtomobili/bmw_x5_2019_67890">
        BMW X5 2019
    </a>
    <meta itemprop="price" content="2300000">
</div>
<div data-marker="item" itemtype="http://schema.org/Product">
    <a data-marker="item-title" href="/moskva/avtomobili/kia_k5_2022_11111">
        Kia K5 2022
    </a>
    <meta itemprop="price" content="950000">
</div>
<a data-marker="pagination-button/nextPage" href="?p=2">Next</a>
</body>
</html>"""


@pytest.fixture
def mock_listing_html_json():
    """Mock listing page with JSON-embedded data."""
    return """<!DOCTYPE html>
<html>
<head>
<script type="application/json">
{
    "catalog": {
        "items": [
            {
                "id": 99001,
                "title": "Honda Civic 2021",
                "urlPath": "/moskva/avtomobili/honda_civic_2021_99001",
                "price": 1800000
            },
            {
                "id": 99002,
                "title": "Mazda 3 2020",
                "urlPath": "/spb/avtomobili/mazda_3_2020_99002",
                "price": 1600000
            }
        ]
    }
}
</script>
</head>
<body></body>
</html>"""
