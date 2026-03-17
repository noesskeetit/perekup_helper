from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import delete, select

from bot.db.models import Filter
from bot.db.session import async_session

router = Router()


class FilterSetup(StatesGroup):
    brand = State()
    model = State()
    max_price = State()
    min_discount = State()
    confirm = State()


# ── /filters — show current filters and options ──────────────────────

@router.message(Command("filters"))
async def cmd_filters(message: Message, state: FSMContext) -> None:
    await state.clear()
    uid = message.from_user.id

    async with async_session() as session:
        result = await session.execute(
            select(Filter).where(Filter.telegram_id == uid)
        )
        filters = result.scalars().all()

    if filters:
        lines = ["Твои текущие фильтры:\n"]
        for i, f in enumerate(filters, 1):
            parts = []
            if f.brand:
                parts.append(f"марка: {f.brand}")
            if f.model:
                parts.append(f"модель: {f.model}")
            if f.max_price is not None:
                parts.append(f"макс. цена: {f.max_price:,.0f}")
            if f.min_discount is not None:
                parts.append(f"мин. дисконт: {f.min_discount}%")
            lines.append(f"{i}. {', '.join(parts) if parts else 'без ограничений'}")
        text = "\n".join(lines)
    else:
        text = "У тебя пока нет фильтров."

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить фильтр", callback_data="filter_add")],
            [InlineKeyboardButton(text="🗑 Удалить все фильтры", callback_data="filter_clear")],
        ]
    )
    await message.answer(text, reply_markup=kb)


# ── Add filter flow ──────────────────────────────────────────────────

@router.callback_query(F.data == "filter_add")
async def cb_filter_add(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(FilterSetup.brand)
    await callback.message.answer(
        "Введи марку автомобиля (например: Toyota).\n"
        "Отправь «-» чтобы пропустить."
    )


@router.message(FilterSetup.brand)
async def process_brand(message: Message, state: FSMContext) -> None:
    value = message.text.strip()
    await state.update_data(brand=None if value == "-" else value)
    await state.set_state(FilterSetup.model)
    await message.answer(
        "Введи модель (например: Camry).\n"
        "Отправь «-» чтобы пропустить."
    )


@router.message(FilterSetup.model)
async def process_model(message: Message, state: FSMContext) -> None:
    value = message.text.strip()
    await state.update_data(model=None if value == "-" else value)
    await state.set_state(FilterSetup.max_price)
    await message.answer(
        "Введи максимальную цену (число, например: 2000000).\n"
        "Отправь «-» чтобы пропустить."
    )


@router.message(FilterSetup.max_price)
async def process_max_price(message: Message, state: FSMContext) -> None:
    value = message.text.strip()
    if value == "-":
        await state.update_data(max_price=None)
    else:
        try:
            price = float(value.replace(" ", "").replace(",", ""))
            if price <= 0:
                raise ValueError
            await state.update_data(max_price=price)
        except ValueError:
            await message.answer("Некорректная цена. Введи положительное число или «-».")
            return

    await state.set_state(FilterSetup.min_discount)
    await message.answer(
        "Введи минимальный дисконт от рыночной цены в % (например: 10).\n"
        "Отправь «-» чтобы пропустить."
    )


@router.message(FilterSetup.min_discount)
async def process_min_discount(message: Message, state: FSMContext) -> None:
    value = message.text.strip()
    if value == "-":
        await state.update_data(min_discount=None)
    else:
        try:
            discount = float(value.replace("%", "").strip())
            if discount < 0 or discount > 100:
                raise ValueError
            await state.update_data(min_discount=discount)
        except ValueError:
            await message.answer("Некорректный дисконт. Введи число от 0 до 100 или «-».")
            return

    data = await state.get_data()
    await state.set_state(FilterSetup.confirm)

    summary_parts = []
    if data.get("brand"):
        summary_parts.append(f"Марка: {data['brand']}")
    if data.get("model"):
        summary_parts.append(f"Модель: {data['model']}")
    if data.get("max_price") is not None:
        summary_parts.append(f"Макс. цена: {data['max_price']:,.0f}")
    if data.get("min_discount") is not None:
        summary_parts.append(f"Мин. дисконт: {data['min_discount']}%")

    summary = "\n".join(summary_parts) if summary_parts else "Без ограничений (все объявления)"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Сохранить", callback_data="filter_save"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="filter_cancel"),
            ]
        ]
    )
    await message.answer(f"Твой новый фильтр:\n\n{summary}", reply_markup=kb)


@router.callback_query(F.data == "filter_save", FilterSetup.confirm)
async def cb_filter_save(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()

    async with async_session() as session:
        new_filter = Filter(
            telegram_id=callback.from_user.id,
            brand=data.get("brand"),
            model=data.get("model"),
            max_price=data.get("max_price"),
            min_discount=data.get("min_discount"),
        )
        session.add(new_filter)
        await session.commit()

    await state.clear()
    await callback.message.answer("Фильтр сохранён! Используй /filters для просмотра.")


@router.callback_query(F.data == "filter_cancel")
async def cb_filter_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await callback.message.answer("Создание фильтра отменено.")


# ── Clear all filters ────────────────────────────────────────────────

@router.callback_query(F.data == "filter_clear")
async def cb_filter_clear(callback: CallbackQuery) -> None:
    await callback.answer()
    uid = callback.from_user.id
    async with async_session() as session:
        await session.execute(
            delete(Filter).where(Filter.telegram_id == uid)
        )
        await session.commit()
    await callback.message.answer("Все фильтры удалены.")
