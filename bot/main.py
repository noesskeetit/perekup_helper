"""Entry point for the Telegram notification bot."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from bot.config import settings
from bot.db.session import init_db
from bot.handlers import filters, start, stats
from bot.services.notifier import run_notifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    await init_db()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=None),
    )
    dp = Dispatcher()

    dp.include_router(start.router)
    dp.include_router(filters.router)
    dp.include_router(stats.router)

    notifier_task = asyncio.create_task(run_notifier(bot))

    try:
        logger.info("Bot started")
        await dp.start_polling(bot)
    finally:
        notifier_task.cancel()
        await notifier_task
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
