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
            await session.scalar(
                select(func.count()).select_from(Filter).where(Filter.telegram_id == uid)
            )
        ) or 0

        notifications_count = (
            await session.scalar(
                select(func.count())
                .select_from(NotificationLog)
                .where(NotificationLog.telegram_id == uid)
            )
        ) or 0

    await message.answer(
        f"📊 Твоя статистика:\n\n"
        f"Активных фильтров: {filters_count}\n"
        f"Отправлено уведомлений: {notifications_count}"
    )
