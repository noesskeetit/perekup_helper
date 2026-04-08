from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import func, select

from bot.db.models import Filter, NotificationLog
from bot.db.session import async_session

router = Router()


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    uid = message.from_user.id
    async with async_session() as session:
        filters_count = (
            await session.scalar(select(func.count()).select_from(Filter).where(Filter.telegram_id == uid))
        ) or 0

        notifications_count = (
            await session.scalar(
                select(func.count()).select_from(NotificationLog).where(NotificationLog.telegram_id == uid)
            )
        ) or 0

    # Fetch system stats from API
    sys_stats = ""
    try:
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.get("http://localhost:8000/api/stats", timeout=5)
            if resp.status_code == 200:
                d = resp.json()
                sys_stats = (
                    f"\n\n📈 Система:\n"
                    f"Листингов: {d.get('unique_listings', '?'):,}\n"
                    f"Горячих сделок: {d.get('hot_deals_count', '?'):,}\n"
                    f"Модель MAPE: {d.get('model_info', {}).get('p50_mape', '?')}%"
                )
    except Exception:
        pass

    await message.answer(
        f"📊 Твоя статистика:\n\n"
        f"Активных фильтров: {filters_count}\n"
        f"Отправлено уведомлений: {notifications_count}"
        f"{sys_stats}"
    )
