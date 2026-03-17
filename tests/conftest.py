import uuid
from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.session import get_session
from app.main import app


@dataclass
class MockAnalysis:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    listing_id: uuid.UUID = field(default_factory=uuid.uuid4)
    category: str = "clean"
    confidence: float = 0.95
    ai_summary: str = "Чистая машина, документы в порядке"
    flags: list = field(default_factory=list)


@dataclass
class MockListing:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    source: str = "avito"
    external_id: str = "123456"
    brand: str = "Toyota"
    model: str = "Camry"
    year: int = 2020
    mileage: int = 50000
    price: int = 2000000
    market_price: int = 2200000
    price_diff_pct: float = -9.1
    description: str = "Отличная машина"
    url: str = "https://avito.ru/123"
    photos: list = field(default_factory=lambda: ["https://img.example.com/1.jpg"])
    analysis: Optional[MockAnalysis] = None


@pytest.fixture
def sample_listings():
    listing1 = MockListing(brand="Toyota", model="Camry", year=2020, price=2000000)
    listing1.analysis = MockAnalysis(listing_id=listing1.id, category="clean", confidence=0.95)

    listing2 = MockListing(brand="BMW", model="X5", year=2019, price=3500000, price_diff_pct=8.5)
    listing2.analysis = MockAnalysis(
        listing_id=listing2.id, category="damaged_body", confidence=0.72, flags=["Следы ремонта"]
    )

    listing3 = MockListing(brand="Kia", model="Rio", year=2021, price=1200000, photos=[])

    return [listing1, listing2, listing3]


def _build_listings_result(items):
    """Build mock result that supports result.scalars().all() chain."""
    scalars_mock = MagicMock()
    scalars_mock.all = MagicMock(return_value=items)

    result = MagicMock()
    result.scalars = MagicMock(return_value=scalars_mock)
    return result


def _make_mock_session(listings):
    mock_session = AsyncMock()

    count_result = MagicMock()
    count_result.scalar = MagicMock(return_value=len(listings))

    listings_result = _build_listings_result(listings)

    brand_set = sorted({item.brand for item in listings})
    brands_result = MagicMock()
    brands_result.all = MagicMock(return_value=[(brand,) for brand in brand_set])

    mock_session.execute = AsyncMock(side_effect=[count_result, listings_result, brands_result])
    return mock_session


def _make_detail_session(listing):
    mock_session = AsyncMock()
    detail_result = MagicMock()
    detail_result.scalar_one_or_none = MagicMock(return_value=listing)
    mock_session.execute = AsyncMock(return_value=detail_result)
    return mock_session


@pytest.fixture
async def client(sample_listings):
    mock_session = _make_mock_session(sample_listings)

    async def override_get_session():
        yield mock_session

    app.dependency_overrides[get_session] = override_get_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def client_empty():
    mock_session = AsyncMock()

    count_result = MagicMock()
    count_result.scalar = MagicMock(return_value=0)

    empty_listings_result = _build_listings_result([])

    brands_result = MagicMock()
    brands_result.all = MagicMock(return_value=[])

    mock_session.execute = AsyncMock(side_effect=[count_result, empty_listings_result, brands_result])

    async def override_get_session():
        yield mock_session

    app.dependency_overrides[get_session] = override_get_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def detail_client(sample_listings):
    mock_session = _make_detail_session(sample_listings[0])

    async def override_get_session():
        yield mock_session

    app.dependency_overrides[get_session] = override_get_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def detail_client_empty():
    mock_session = _make_detail_session(None)

    async def override_get_session():
        yield mock_session

    app.dependency_overrides[get_session] = override_get_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
