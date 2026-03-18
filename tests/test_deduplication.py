"""Tests for app/services/deduplication.py"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.listing import Listing
from app.services.deduplication import _is_fuzzy_match, detect_and_mark_duplicates

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
        url="https://example.com/listing",
        created_at=_now(),
        updated_at=_now(),
    )
    defaults.update(kwargs)
    return Listing(**defaults)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
# _is_fuzzy_match unit tests
# ---------------------------------------------------------------------------


def test_fuzzy_match_identical():
    a = _listing()
    b = _listing()
    assert _is_fuzzy_match(a, b)


def test_fuzzy_match_mileage_within_tolerance():
    a = _listing(mileage=30_000)
    b = _listing(mileage=34_999)  # diff = 4999 < 5000
    assert _is_fuzzy_match(a, b)


def test_fuzzy_no_match_mileage_over_tolerance():
    a = _listing(mileage=30_000)
    b = _listing(mileage=35_001)  # diff = 5001 > 5000
    assert not _is_fuzzy_match(a, b)


def test_fuzzy_match_price_within_tolerance():
    a = _listing(price=1_000_000)
    b = _listing(price=1_099_999)  # ~9.99% diff
    assert _is_fuzzy_match(a, b)


def test_fuzzy_no_match_price_over_tolerance():
    a = _listing(price=1_000_000)
    b = _listing(price=1_200_000)  # ~18% diff
    assert not _is_fuzzy_match(a, b)


def test_fuzzy_no_match_different_brand():
    a = _listing(brand="Toyota")
    b = _listing(brand="BMW")
    assert not _is_fuzzy_match(a, b)


def test_fuzzy_no_match_different_model():
    a = _listing(model="Camry")
    b = _listing(model="RAV4")
    assert not _is_fuzzy_match(a, b)


def test_fuzzy_no_match_different_year():
    a = _listing(year=2020)
    b = _listing(year=2021)
    assert not _is_fuzzy_match(a, b)


def test_fuzzy_match_brand_case_insensitive():
    a = _listing(brand="toyota")
    b = _listing(brand="TOYOTA")
    assert _is_fuzzy_match(a, b)


def test_fuzzy_match_null_mileage_treated_as_zero():
    a = _listing(mileage=None)
    b = _listing(mileage=4_999)
    assert _is_fuzzy_match(a, b)


# ---------------------------------------------------------------------------
# detect_and_mark_duplicates integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_duplicates_no_changes(session):
    listings = [
        _listing(brand="Toyota", model="Camry", year=2020),
        _listing(brand="BMW", model="X5", year=2019),
    ]
    for lst in listings:
        session.add(lst)
    await session.commit()

    marked = await detect_and_mark_duplicates(session)
    assert marked == 0
    for lst in listings:
        await session.refresh(lst)
        assert lst.is_duplicate is False


@pytest.mark.asyncio
async def test_vin_duplicates_marked(session):
    vin = "JTDBR40E600123456"
    t1 = _now()
    # older listing (canonical)
    canonical = _listing(vin=vin, source="avito", created_at=t1)
    # newer listing (duplicate)
    import asyncio

    await asyncio.sleep(0.001)  # ensure different created_at ordering
    t2 = _now()
    duplicate = _listing(vin=vin, source="autoru", created_at=t2)

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
async def test_fuzzy_duplicates_marked(session):
    from datetime import timedelta

    t_old = datetime(2026, 1, 1, tzinfo=UTC)
    t_new = t_old + timedelta(hours=1)

    canonical = _listing(
        brand="Toyota",
        model="Camry",
        year=2020,
        price=1_500_000,
        mileage=30_000,
        source="avito",
        vin=None,
        created_at=t_old,
    )
    duplicate = _listing(
        brand="toyota",
        model="camry",
        year=2020,
        price=1_520_000,  # ~1.3% diff
        mileage=31_000,  # 1000 km diff
        source="autoru",
        vin=None,
        created_at=t_new,
    )

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
    from datetime import timedelta

    vin = "ALREADYMARKEDVIN123"
    t_old = datetime(2026, 1, 1, tzinfo=UTC)
    t_new = t_old + timedelta(hours=1)

    canonical = _listing(vin=vin, created_at=t_old)
    duplicate = _listing(vin=vin, created_at=t_new)
    session.add(canonical)
    session.add(duplicate)
    await session.commit()

    # First pass
    marked1 = await detect_and_mark_duplicates(session)
    assert marked1 == 1

    # Second pass should not re-count
    marked2 = await detect_and_mark_duplicates(session)
    assert marked2 == 0


@pytest.mark.asyncio
async def test_multiple_vin_groups(session):
    from datetime import timedelta

    vin_a = "VINAAAA0000000001"
    vin_b = "VINBBBB0000000002"
    t0 = datetime(2026, 1, 1, tzinfo=UTC)

    for i, (vin, source) in enumerate([(vin_a, "avito"), (vin_a, "autoru"), (vin_b, "avito"), (vin_b, "autoru")]):
        session.add(_listing(vin=vin, source=source, created_at=t0 + timedelta(hours=i)))
    await session.commit()

    marked = await detect_and_mark_duplicates(session)
    assert marked == 2  # one duplicate per VIN group


@pytest.mark.asyncio
async def test_no_duplicate_when_price_too_different(session):
    from datetime import timedelta

    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    a = _listing(price=1_000_000, vin=None, created_at=t0)
    b = _listing(price=2_000_000, vin=None, created_at=t0 + timedelta(hours=1))
    session.add(a)
    session.add(b)
    await session.commit()

    marked = await detect_and_mark_duplicates(session)
    assert marked == 0
