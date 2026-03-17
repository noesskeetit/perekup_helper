"""Tests for bot command handlers (/start, /stop, /filters, /stats)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select

from bot.db.models import Filter, NotificationLog, User
from bot.handlers.filters import FilterSetup
from bot.handlers.start import cmd_start, cmd_stop
from bot.handlers.stats import cmd_stats


def _make_message(user_id: int = 100, text: str = "") -> AsyncMock:
    msg = AsyncMock()
    msg.from_user = AsyncMock()
    msg.from_user.id = user_id
    msg.text = text
    return msg


def _make_callback(user_id: int = 100, data: str = "") -> AsyncMock:
    cb = AsyncMock()
    cb.from_user = AsyncMock()
    cb.from_user.id = user_id
    cb.data = data
    cb.message = AsyncMock()
    return cb


def _make_state(storage: MemoryStorage | None = None) -> FSMContext:
    storage = storage or MemoryStorage()
    return FSMContext(
        storage=storage,
        key=StorageKey(bot_id=1, chat_id=100, user_id=100),
    )


# ── /start ──────────────────────────────────────────────────────


class TestCmdStart:
    @pytest.mark.asyncio
    async def test_start_new_user(self, db_session):
        msg = _make_message(user_id=1001)

        with patch("bot.handlers.start.async_session", return_value=db_session):
            await cmd_start(msg)

        msg.answer.assert_awaited_once()
        text = msg.answer.call_args.args[0]
        assert "/filters" in text
        assert "/stats" in text

        user = await db_session.get(User, 1001)
        assert user is not None
        assert user.is_active is True

    @pytest.mark.asyncio
    async def test_start_existing_active_user(self, db_session):
        db_session.add(User(telegram_id=1002, is_active=True))
        await db_session.commit()

        msg = _make_message(user_id=1002)
        with patch("bot.handlers.start.async_session", return_value=db_session):
            await cmd_start(msg)

        text = msg.answer.call_args.args[0]
        assert "С возвращением" in text

    @pytest.mark.asyncio
    async def test_start_reactivates_inactive_user(self, db_session):
        db_session.add(User(telegram_id=1003, is_active=False))
        await db_session.commit()

        msg = _make_message(user_id=1003)
        with patch("bot.handlers.start.async_session", return_value=db_session):
            await cmd_start(msg)

        user = await db_session.get(User, 1003)
        assert user.is_active is True


# ── /stop ───────────────────────────────────────────────────────


class TestCmdStop:
    @pytest.mark.asyncio
    async def test_stop_deactivates_user(self, db_session):
        db_session.add(User(telegram_id=2001, is_active=True))
        await db_session.commit()

        msg = _make_message(user_id=2001)
        with patch("bot.handlers.start.async_session", return_value=db_session):
            await cmd_stop(msg)

        user = await db_session.get(User, 2001)
        assert user.is_active is False

    @pytest.mark.asyncio
    async def test_stop_unregistered_user(self, db_session):
        msg = _make_message(user_id=2002)
        with patch("bot.handlers.start.async_session", return_value=db_session):
            await cmd_stop(msg)

        text = msg.answer.call_args.args[0]
        assert "/start" in text


# ── /stats ──────────────────────────────────────────────────────


class TestCmdStats:
    @pytest.mark.asyncio
    async def test_stats_empty(self, db_session):
        msg = _make_message(user_id=3001)
        with patch("bot.handlers.stats.async_session", return_value=db_session):
            await cmd_stats(msg)

        text = msg.answer.call_args.args[0]
        assert "0" in text

    @pytest.mark.asyncio
    async def test_stats_with_data(self, db_session):
        db_session.add(Filter(telegram_id=3002, brand="Toyota"))
        db_session.add(Filter(telegram_id=3002, model="Camry"))
        db_session.add(
            NotificationLog(telegram_id=3002, listing_url="https://example.com/1")
        )
        await db_session.commit()

        msg = _make_message(user_id=3002)
        with patch("bot.handlers.stats.async_session", return_value=db_session):
            await cmd_stats(msg)

        text = msg.answer.call_args.args[0]
        assert "2" in text  # 2 filters
        assert "1" in text  # 1 notification


# ── /filters ────────────────────────────────────────────────────


class TestCmdFilters:
    @pytest.mark.asyncio
    async def test_filters_empty(self, db_session):
        msg = _make_message(user_id=4001)
        state = _make_state()

        with patch("bot.handlers.filters.async_session", return_value=db_session):
            from bot.handlers.filters import cmd_filters

            await cmd_filters(msg, state)

        text = msg.answer.call_args.args[0]
        assert "нет фильтров" in text

    @pytest.mark.asyncio
    async def test_filters_shows_existing(self, db_session):
        db_session.add(
            Filter(telegram_id=4002, brand="BMW", max_price=3_000_000)
        )
        await db_session.commit()

        msg = _make_message(user_id=4002)
        state = _make_state()

        with patch("bot.handlers.filters.async_session", return_value=db_session):
            from bot.handlers.filters import cmd_filters

            await cmd_filters(msg, state)

        text = msg.answer.call_args.args[0]
        assert "BMW" in text

    @pytest.mark.asyncio
    async def test_filter_add_flow(self, db_session):
        from bot.handlers.filters import (
            cb_filter_add,
            cb_filter_save,
            process_brand,
            process_max_price,
            process_min_discount,
            process_model,
        )

        storage = MemoryStorage()
        state = _make_state(storage)

        # Step 1: start add flow
        cb = _make_callback(user_id=100, data="filter_add")
        await cb_filter_add(cb, state)
        current = await state.get_state()
        assert current == FilterSetup.brand

        # Step 2: enter brand
        msg = _make_message(user_id=100, text="Toyota")
        await process_brand(msg, state)
        current = await state.get_state()
        assert current == FilterSetup.model

        # Step 3: enter model
        msg = _make_message(user_id=100, text="Camry")
        await process_model(msg, state)
        current = await state.get_state()
        assert current == FilterSetup.max_price

        # Step 4: enter max price
        msg = _make_message(user_id=100, text="2000000")
        await process_max_price(msg, state)
        current = await state.get_state()
        assert current == FilterSetup.min_discount

        # Step 5: enter min discount
        msg = _make_message(user_id=100, text="10")
        await process_min_discount(msg, state)
        current = await state.get_state()
        assert current == FilterSetup.confirm

        data = await state.get_data()
        assert data["brand"] == "Toyota"
        assert data["model"] == "Camry"
        assert data["max_price"] == 2_000_000.0
        assert data["min_discount"] == 10.0

        # Step 6: save
        cb = _make_callback(user_id=100, data="filter_save")
        with patch("bot.handlers.filters.async_session", return_value=db_session):
            await cb_filter_save(cb, state)

        result = await db_session.execute(
            select(Filter).where(Filter.telegram_id == 100)
        )
        saved = result.scalars().all()
        assert len(saved) == 1
        assert saved[0].brand == "Toyota"
        assert saved[0].model == "Camry"
        assert saved[0].max_price == 2_000_000.0
        assert saved[0].min_discount == 10.0

    @pytest.mark.asyncio
    async def test_filter_skip_fields(self, db_session):
        from bot.handlers.filters import (
            cb_filter_add,
            cb_filter_save,
            process_brand,
            process_max_price,
            process_min_discount,
            process_model,
        )

        storage = MemoryStorage()
        state = _make_state(storage)

        cb = _make_callback(user_id=100, data="filter_add")
        await cb_filter_add(cb, state)

        # Skip all fields with "-"
        msg = _make_message(user_id=100, text="-")
        await process_brand(msg, state)
        msg = _make_message(user_id=100, text="-")
        await process_model(msg, state)
        msg = _make_message(user_id=100, text="-")
        await process_max_price(msg, state)
        msg = _make_message(user_id=100, text="-")
        await process_min_discount(msg, state)

        data = await state.get_data()
        assert data["brand"] is None
        assert data["model"] is None
        assert data["max_price"] is None
        assert data["min_discount"] is None

        cb = _make_callback(user_id=100, data="filter_save")
        with patch("bot.handlers.filters.async_session", return_value=db_session):
            await cb_filter_save(cb, state)

        result = await db_session.execute(
            select(Filter).where(Filter.telegram_id == 100)
        )
        saved = result.scalars().all()
        assert len(saved) == 1
        assert saved[0].brand is None

    @pytest.mark.asyncio
    async def test_filter_invalid_price(self):
        from bot.handlers.filters import process_max_price

        storage = MemoryStorage()
        state = _make_state(storage)
        await state.set_state(FilterSetup.max_price)

        msg = _make_message(user_id=100, text="abc")
        await process_max_price(msg, state)

        # State should NOT advance on invalid input
        current = await state.get_state()
        assert current == FilterSetup.max_price
        msg.answer.assert_awaited_once()
        assert "Некорректная" in msg.answer.call_args.args[0]

    @pytest.mark.asyncio
    async def test_filter_invalid_discount(self):
        from bot.handlers.filters import process_min_discount

        storage = MemoryStorage()
        state = _make_state(storage)
        await state.set_state(FilterSetup.min_discount)

        msg = _make_message(user_id=100, text="150")
        await process_min_discount(msg, state)

        current = await state.get_state()
        assert current == FilterSetup.min_discount
        msg.answer.assert_awaited_once()
        assert "Некорректный" in msg.answer.call_args.args[0]

    @pytest.mark.asyncio
    async def test_filter_cancel(self):
        from bot.handlers.filters import cb_filter_cancel

        storage = MemoryStorage()
        state = _make_state(storage)
        await state.set_state(FilterSetup.confirm)
        await state.update_data(brand="Toyota")

        cb = _make_callback(user_id=100, data="filter_cancel")
        await cb_filter_cancel(cb, state)

        current = await state.get_state()
        assert current is None

    @pytest.mark.asyncio
    async def test_filter_clear_all(self, db_session):
        from bot.handlers.filters import cb_filter_clear

        db_session.add(Filter(telegram_id=100, brand="Toyota"))
        db_session.add(Filter(telegram_id=100, brand="BMW"))
        await db_session.commit()

        cb = _make_callback(user_id=100, data="filter_clear")
        with patch("bot.handlers.filters.async_session", return_value=db_session):
            await cb_filter_clear(cb)

        result = await db_session.execute(
            select(Filter).where(Filter.telegram_id == 100)
        )
        assert len(result.scalars().all()) == 0
