"""Tests for app/services/deduplication.py — cross-source only dedup."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.listing import Listing
from app.services.deduplication import _is_cross_source_match, detect_and_mark_duplicates


def _now() -> datetime:
    return datetime.now(UTC)


def _listing(**kwargs) -> Listing:
    defaults = dict(
        id=uuid.uuid4(),
        source="avito",
        external_id=str(uuid.uuid4()),
        brand="Toyota",
        model="Camry",
        year=2020,
        price=1_500_000,
        mileage=30_000,
        city="Москва",
        url="https://example.com/listing",
        created_at=_now(),
        updated_at=_now(),
    )
    defaults.update(kwargs)
    return Listing(**defaults)


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


# ---------------------------------------------------------------------------
# _is_cross_source_match unit tests
# ---------------------------------------------------------------------------


def test_match_cross_source_identical():
    a = _listing(source="avito", city="Москва")
    b = _listing(source="drom", city="Москва")
    assert _is_cross_source_match(a, b)


def test_no_match_same_source():
    """Same source should NEVER match — dedup within source is not needed."""
    a = _listing(source="avito")
    b = _listing(source="avito")
    assert not _is_cross_source_match(a, b)


def test_no_match_different_brand():
    a = _listing(source="avito", brand="Toyota")
    b = _listing(source="drom", brand="BMW")
    assert not _is_cross_source_match(a, b)


def test_no_match_different_model():
    a = _listing(source="avito", model="Camry")
    b = _listing(source="drom", model="RAV4")
    assert not _is_cross_source_match(a, b)


def test_no_match_different_year():
    a = _listing(source="avito", year=2020)
    b = _listing(source="drom", year=2021)
    assert not _is_cross_source_match(a, b)


def test_no_match_different_city():
    a = _listing(source="avito", city="Москва")
    b = _listing(source="drom", city="Санкт-Петербург")
    assert not _is_cross_source_match(a, b)


def test_no_match_missing_city():
    a = _listing(source="avito", city="Москва")
    b = _listing(source="drom", city=None)
    assert not _is_cross_source_match(a, b)


def test_no_match_different_price():
    """Price must match exactly."""
    a = _listing(source="avito", price=1_500_000)
    b = _listing(source="drom", price=1_510_000)
    assert not _is_cross_source_match(a, b)


def test_match_mileage_within_tolerance():
    a = _listing(source="avito", mileage=30_000)
    b = _listing(source="drom", mileage=30_499)
    assert _is_cross_source_match(a, b)


def test_no_match_mileage_over_tolerance():
    a = _listing(source="avito", mileage=30_000)
    b = _listing(source="drom", mileage=30_501)
    assert not _is_cross_source_match(a, b)


def test_match_brand_case_insensitive():
    a = _listing(source="avito", brand="toyota", city="Москва")
    b = _listing(source="drom", brand="TOYOTA", city="Москва")
    assert _is_cross_source_match(a, b)


def test_match_city_case_insensitive():
    a = _listing(source="avito", city="москва")
    b = _listing(source="drom", city="Москва")
    assert _is_cross_source_match(a, b)


# ---------------------------------------------------------------------------
# detect_and_mark_duplicates integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_duplicates_different_cars(session):
    listings = [
        _listing(brand="Toyota", model="Camry", year=2020, source="avito"),
        _listing(brand="BMW", model="X5", year=2019, source="drom"),
    ]
    for lst in listings:
        session.add(lst)
    await session.commit()

    marked = await detect_and_mark_duplicates(session)
    assert marked == 0


@pytest.mark.asyncio
async def test_same_source_never_deduped(session):
    """Two identical listings from same source must NOT be marked as duplicates."""
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    a = _listing(source="avito", city="Москва", created_at=t0)
    b = _listing(source="avito", city="Москва", created_at=t0 + timedelta(hours=1))
    session.add(a)
    session.add(b)
    await session.commit()

    marked = await detect_and_mark_duplicates(session)
    assert marked == 0


@pytest.mark.asyncio
async def test_cross_source_match_marked(session):
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    canonical = _listing(source="avito", city="Москва", created_at=t0)
    duplicate = _listing(source="drom", city="Москва", created_at=t0 + timedelta(hours=1))
    session.add(canonical)
    session.add(duplicate)
    await session.commit()

    marked = await detect_and_mark_duplicates(session)
    assert marked == 1

    await session.refresh(canonical)
    await session.refresh(duplicate)
    assert canonical.is_duplicate is False
    assert duplicate.is_duplicate is True
    assert duplicate.canonical_id == canonical.id


@pytest.mark.asyncio
async def test_already_marked_not_double_counted(session):
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    canonical = _listing(source="avito", city="Москва", created_at=t0)
    duplicate = _listing(source="drom", city="Москва", created_at=t0 + timedelta(hours=1))
    session.add(canonical)
    session.add(duplicate)
    await session.commit()

    marked1 = await detect_and_mark_duplicates(session)
    assert marked1 == 1

    marked2 = await detect_and_mark_duplicates(session)
    assert marked2 == 0


@pytest.mark.asyncio
async def test_no_match_without_city(session):
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    a = _listing(source="avito", city=None, created_at=t0)
    b = _listing(source="drom", city=None, created_at=t0 + timedelta(hours=1))
    session.add(a)
    session.add(b)
    await session.commit()

    marked = await detect_and_mark_duplicates(session)
    assert marked == 0


@pytest.mark.asyncio
async def test_no_duplicate_when_price_different(session):
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    a = _listing(source="avito", price=1_000_000, city="Москва", created_at=t0)
    b = _listing(source="drom", price=1_050_000, city="Москва", created_at=t0 + timedelta(hours=1))
    session.add(a)
    session.add(b)
    await session.commit()

    marked = await detect_and_mark_duplicates(session)
    assert marked == 0
