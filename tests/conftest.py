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


def _make_mock_session(listings):
    mock_session = AsyncMock()

    result_mock = MagicMock()
    result_mock.scalar.return_value = len(listings)
    result_mock.scalars.return_value.all.return_value = listings
    result_mock.scalar_one_or_none.return_value = listings[0] if listings else None
    unique_brands = {item.brand: item for item in listings}.values()
    result_mock.all.return_value = [(item.brand,) for item in unique_brands]

    mock_session.execute = AsyncMock(return_value=result_mock)
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
    result_mock = MagicMock()
    result_mock.scalar.return_value = 0
    result_mock.scalars.return_value.all.return_value = []
    result_mock.scalar_one_or_none.return_value = None
    result_mock.all.return_value = []
    mock_session.execute = AsyncMock(return_value=result_mock)

    async def override_get_session():
        yield mock_session

    app.dependency_overrides[get_session] = override_get_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
