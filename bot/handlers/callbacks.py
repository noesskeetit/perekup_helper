"""Callback handlers for inline keyboard buttons in notifications."""

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import delete, select

from bot.db.models import MutedBrand
from bot.db.session import async_session

router = Router()


@router.message(Command("muted"))
async def cmd_muted(message: Message) -> None:
    """Show and manage muted brands."""
    uid = message.from_user.id
    async with async_session() as session:
        result = await session.execute(select(MutedBrand).where(MutedBrand.telegram_id == uid))
        muted = result.scalars().all()

    if not muted:
        await message.answer("У тебя нет скрытых брендов.")
        return

    lines = ["🔇 <b>Скрытые бренды:</b>\n"]
    buttons = []
    for m in muted:
        lines.append(f"  • {m.brand}")
        buttons.append([InlineKeyboardButton(text=f"✅ Вернуть {m.brand}", callback_data=f"unmute:{m.brand}")])

    buttons.append([InlineKeyboardButton(text="🗑 Очистить все", callback_data="unmute_all")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=kb)


@router.callback_query(lambda c: c.data and c.data.startswith("mute:"))
async def callback_mute_brand(callback: CallbackQuery) -> None:
    """Mute a brand — stop receiving notifications for it."""
    brand = callback.data.split(":", 1)[1]
    telegram_id = callback.from_user.id

    async with async_session() as session:
        existing = await session.get(MutedBrand, (telegram_id, brand.lower()))
        if existing is None:
            session.add(MutedBrand(telegram_id=telegram_id, brand=brand.lower()))
            await session.commit()

    await callback.answer(f"{brand} скрыт. Используй /muted чтобы управлять списком.")
    await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(lambda c: c.data and c.data.startswith("more:"))
async def callback_more_like_this(callback: CallbackQuery) -> None:
    """Search for more listings of the same brand+model."""
    parts = callback.data.split(":")
    brand = parts[1] if len(parts) > 1 else ""
    model = parts[2] if len(parts) > 2 else ""

    try:
        import httpx

        params = {"brand": brand, "limit": 5}
        if model:
            params["model"] = model

        async with httpx.AsyncClient() as client:
            resp = await client.get("http://localhost:8000/api/search", params=params, timeout=10)
            if resp.status_code != 200:
                await callback.answer("Сервер недоступен")
                return
            results = resp.json()
    except Exception:
        await callback.answer("Ошибка поиска")
        return

    if not results:
        await callback.answer(f"Нет других {brand} {model}")
        return

    query = f"{brand} {model}" if model else brand
    lines = [f"🔍 <b>Ещё {query}:</b>\n"]
    for i, r in enumerate(results[:5], 1):
        diff = r.get("diff_pct")
        diff_str = f"  📉 {diff:+.0f}%" if diff else ""
        score = r.get("deal_score")
        score_str = f"  ⭐{score:.0f}" if score else ""
        lines.append(f"{i}. <b>{r['brand']} {r['model']}</b> {r['year']}")
        lines.append(f"   💰 {r['price']:,.0f}₽{diff_str}{score_str}")
        url = r.get("url", "")
        if url:
            lines.append(f"   🔗 {url}")
        lines.append("")

    await callback.message.answer("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("unmute:"))
async def callback_unmute_brand(callback: CallbackQuery) -> None:
    """Unmute a brand — restore notifications for it."""
    brand = callback.data.split(":", 1)[1]
    telegram_id = callback.from_user.id

    async with async_session() as session:
        await session.execute(
            delete(MutedBrand).where(MutedBrand.telegram_id == telegram_id, MutedBrand.brand == brand.lower())
        )
        await session.commit()

    await callback.answer(f"{brand} возвращён в уведомления")
    await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(lambda c: c.data == "unmute_all")
async def callback_unmute_all(callback: CallbackQuery) -> None:
    """Unmute all brands."""
    telegram_id = callback.from_user.id

    async with async_session() as session:
        await session.execute(delete(MutedBrand).where(MutedBrand.telegram_id == telegram_id))
        await session.commit()

    await callback.answer("Все бренды возвращены")
    await callback.message.edit_text("🔇 Список скрытых брендов очищен.")
