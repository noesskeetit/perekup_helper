from __future__ import annotations

import pytest
from sqlalchemy import select

from bot.db.models import Filter, NotificationLog, User


@pytest.mark.asyncio
async def test_create_user(db_session):
    user = User(telegram_id=123456, is_active=True)
    db_session.add(user)
    await db_session.commit()

    result = await db_session.get(User, 123456)
    assert result is not None
    assert result.telegram_id == 123456
    assert result.is_active is True


@pytest.mark.asyncio
async def test_create_filter(db_session):
    f = Filter(
        telegram_id=123456,
        brand="Toyota",
        model="Camry",
        max_price=2_000_000,
        min_discount=10.0,
    )
    db_session.add(f)
    await db_session.commit()

    result = await db_session.execute(select(Filter).where(Filter.telegram_id == 123456))
    filters = result.scalars().all()
    assert len(filters) == 1
    assert filters[0].brand == "Toyota"
    assert filters[0].max_price == 2_000_000


@pytest.mark.asyncio
async def test_create_notification_log(db_session):
    log = NotificationLog(telegram_id=123456, listing_url="https://example.com/1")
    db_session.add(log)
    await db_session.commit()

    result = await db_session.execute(select(NotificationLog).where(NotificationLog.telegram_id == 123456))
    logs = result.scalars().all()
    assert len(logs) == 1
    assert logs[0].listing_url == "https://example.com/1"
