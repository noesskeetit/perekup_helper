from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select

from bot.db.models import Filter, User
from bot.db.session import async_session

router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    async with async_session() as session:
        user = await session.get(User, message.from_user.id)
        if user is None:
            user = User(telegram_id=message.from_user.id, is_active=True)
            session.add(user)
            await session.commit()
            await message.answer(
                "Привет! Я бот для поиска выгодных авто-объявлений.\n\n"
                "Настрой фильтры командой /filters, и я буду присылать "
                "уведомления о новых интересных предложениях.\n\n"
                "Команды:\n"
                "/filters — настроить фильтры\n"
                "/stats — статистика уведомлений\n"
                "/stop — приостановить уведомления"
            )
        else:
            if not user.is_active:
                user.is_active = True
                await session.commit()
            await message.answer(
                "С возвращением! Уведомления активны.\n"
                "Используй /filters для настройки фильтров."
            )


@router.message(Command("stop"))
async def cmd_stop(message: Message) -> None:
    async with async_session() as session:
        user = await session.get(User, message.from_user.id)
        if user is None:
            await message.answer("Ты ещё не зарегистрирован. Отправь /start.")
            return
        user.is_active = False
        await session.commit()
    await message.answer(
        "Уведомления приостановлены. Отправь /start, чтобы возобновить."
    )
