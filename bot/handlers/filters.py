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
    city = State()
    min_year = State()
    min_deal_score = State()
    confirm = State()


# ── /filters — show current filters and options ──────────────────────


@router.message(Command("filters"))
async def cmd_filters(message: Message, state: FSMContext) -> None:
    await state.clear()
    uid = message.from_user.id

    async with async_session() as session:
        result = await session.execute(select(Filter).where(Filter.telegram_id == uid))
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
            if f.city:
                parts.append(f"город: {f.city}")
            if f.min_year is not None:
                parts.append(f"от {f.min_year} г.")
            if f.min_deal_score is not None:
                parts.append(f"оценка >= {f.min_deal_score}")
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
    await callback.message.answer("Введи марку автомобиля (например: Toyota).\nОтправь «-» чтобы пропустить.")


@router.message(FilterSetup.brand)
async def process_brand(message: Message, state: FSMContext) -> None:
    value = message.text.strip()
    await state.update_data(brand=None if value == "-" else value)
    await state.set_state(FilterSetup.model)
    await message.answer("Введи модель (например: Camry).\nОтправь «-» чтобы пропустить.")


@router.message(FilterSetup.model)
async def process_model(message: Message, state: FSMContext) -> None:
    value = message.text.strip()
    await state.update_data(model=None if value == "-" else value)
    await state.set_state(FilterSetup.max_price)
    await message.answer("Введи максимальную цену (число, например: 2000000).\nОтправь «-» чтобы пропустить.")


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
        "Введи минимальный дисконт от рыночной цены в % (например: 10).\nОтправь «-» чтобы пропустить."
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

    await state.set_state(FilterSetup.city)
    await message.answer("Введи город (например: Москва).\nОтправь «-» чтобы пропустить.")


@router.message(FilterSetup.city)
async def process_city(message: Message, state: FSMContext) -> None:
    value = message.text.strip()
    await state.update_data(city=None if value == "-" else value)
    await state.set_state(FilterSetup.min_year)
    await message.answer("Введи минимальный год выпуска (например: 2018).\nОтправь «-» чтобы пропустить.")


@router.message(FilterSetup.min_year)
async def process_min_year(message: Message, state: FSMContext) -> None:
    value = message.text.strip()
    if value == "-":
        await state.update_data(min_year=None)
    else:
        try:
            year = int(value)
            if year < 1990 or year > 2030:
                raise ValueError
            await state.update_data(min_year=year)
        except ValueError:
            await message.answer("Некорректный год. Введи число от 1990 до 2030 или «-».")
            return

    await state.set_state(FilterSetup.min_deal_score)
    await message.answer("Введи минимальную оценку сделки 0-100 (рекомендую 60+).\nОтправь «-» чтобы пропустить.")


@router.message(FilterSetup.min_deal_score)
async def process_min_deal_score(message: Message, state: FSMContext) -> None:
    value = message.text.strip()
    if value == "-":
        await state.update_data(min_deal_score=None)
    else:
        try:
            score = float(value)
            if score < 0 or score > 100:
                raise ValueError
            await state.update_data(min_deal_score=score)
        except ValueError:
            await message.answer("Некорректная оценка. Введи число от 0 до 100 или «-».")
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
    if data.get("city"):
        summary_parts.append(f"Город: {data['city']}")
    if data.get("min_year") is not None:
        summary_parts.append(f"Мин. год: {data['min_year']}")
    if data.get("min_deal_score") is not None:
        summary_parts.append(f"Мин. оценка: {data['min_deal_score']}")

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
            city=data.get("city"),
            min_year=data.get("min_year"),
            min_deal_score=data.get("min_deal_score"),
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
        await session.execute(delete(Filter).where(Filter.telegram_id == uid))
        await session.commit()
    await callback.message.answer("Все фильтры удалены.")
