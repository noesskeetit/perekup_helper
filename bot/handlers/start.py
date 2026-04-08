from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.db.models import User
from bot.db.session import async_session

router = Router()

HELP_TEXT = (
    "🚗 <b>PerekupHelper Bot</b>\n\n"
    "Я ищу выгодные авто-объявления на Avito, Drom и Auto.ru.\n"
    "Настрой фильтры — и я пришлю уведомление, когда появится подходящая машина.\n\n"
    "<b>Команды:</b>\n"
    "/filters — настроить фильтры (марка, модель, цена, скидка)\n"
    "/deals — топ-5 горячих предложений прямо сейчас\n"
    "/drops — свежие снижения цен\n"
    "/stats — статистика уведомлений\n"
    "/stop — приостановить уведомления\n"
    "/help — эта справка"
)


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    async with async_session() as session:
        user = await session.get(User, message.from_user.id)
        if user is None:
            user = User(telegram_id=message.from_user.id, is_active=True)
            session.add(user)
            await session.commit()
            await message.answer(HELP_TEXT, parse_mode="HTML")
        else:
            if not user.is_active:
                user.is_active = True
                await session.commit()
            await message.answer("С возвращением! Уведомления активны.\nИспользуй /filters для настройки фильтров.")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, parse_mode="HTML")


@router.message(Command("stop"))
async def cmd_stop(message: Message) -> None:
    async with async_session() as session:
        user = await session.get(User, message.from_user.id)
        if user is None:
            await message.answer("Ты ещё не зарегистрирован. Отправь /start.")
            return
        user.is_active = False
        await session.commit()
    await message.answer("Уведомления приостановлены. Отправь /start, чтобы возобновить.")


@router.message(Command("deals"))
async def cmd_deals(message: Message) -> None:
    """Show top 5 hot deals right now."""
    try:
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.get("http://localhost:8000/api/top-deals", timeout=10)
            if resp.status_code != 200:
                await message.answer("Сервер недоступен. Попробуй позже.")
                return
            data = resp.json()
    except Exception:
        await message.answer("Не удалось получить данные. Попробуй позже.")
        return

    deals = data.get("deals", data) if isinstance(data, dict) else data
    if not deals:
        await message.answer("Сейчас нет горячих предложений.")
        return

    lines = ["🔥 <b>Топ-5 горячих предложений:</b>\n"]
    for i, deal in enumerate(deals[:5], 1):
        brand = deal.get("brand", "?")
        model = deal.get("model", "?")
        year = deal.get("year", "?")
        price = deal.get("price", 0)
        diff = deal.get("price_diff_pct", 0)
        url = deal.get("url", "")
        lines.append(f"{i}. <b>{brand} {model}</b> {year}")
        lines.append(f"   💰 {price:,.0f}₽  📉 {diff:+.0f}%")
        if url:
            lines.append(f"   🔗 {url}")
        lines.append("")

    await message.answer("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)


@router.message(Command("drops"))
async def cmd_drops(message: Message) -> None:
    """Show recent price drops."""
    try:
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.get("http://localhost:8000/api/price-drops", timeout=10)
            if resp.status_code != 200:
                await message.answer("Сервер недоступен. Попробуй позже.")
                return
            data = resp.json()
    except Exception:
        await message.answer("Не удалось получить данные. Попробуй позже.")
        return

    drops = data if isinstance(data, list) else data.get("drops", [])
    if not drops:
        await message.answer("Нет свежих снижений цен.")
        return

    lines = ["📉 <b>Свежие снижения цен:</b>\n"]
    for i, drop in enumerate(drops[:5], 1):
        brand = drop.get("brand", "?")
        model = drop.get("model", "?")
        year = drop.get("year", "?")
        price = drop.get("price", 0)
        drop_pct = drop.get("price_drop_pct", 0)
        url = drop.get("url", "")
        lines.append(f"{i}. <b>{brand} {model}</b> {year}")
        lines.append(f"   💰 {price:,.0f}₽  📉 Снижение: {drop_pct:.0f}%")
        if url:
            lines.append(f"   🔗 {url}")
        lines.append("")

    await message.answer("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)
