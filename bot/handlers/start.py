import contextlib

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
    "/search Toyota Camry — поиск по марке/модели\n"
    "/calc Toyota Camry 2020 — оценка рыночной цены\n"
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
        diff = deal.get("diff_pct", deal.get("price_diff_pct", 0))
        score = deal.get("deal_score", 0)
        market = deal.get("market_price")
        url = deal.get("url", "")
        lines.append(f"{i}. <b>{brand} {model}</b> {year}  ⭐{score}")
        market_str = f"  (рынок ~{market:,.0f}₽)" if market else ""
        lines.append(f"   💰 {price:,.0f}₽{market_str}")
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


@router.message(Command("search"))
async def cmd_search(message: Message) -> None:
    """Search listings by brand and model. Usage: /search Toyota Camry"""
    args = message.text.strip().split(maxsplit=2)
    if len(args) < 2:
        await message.answer("Использование: /search <марка> [модель]\nПример: /search Toyota Camry")
        return

    brand = args[1]
    model_name = args[2] if len(args) > 2 else None

    try:
        import httpx

        params = {"brand": brand, "limit": 5}
        if model_name:
            params["model"] = model_name

        async with httpx.AsyncClient() as client:
            resp = await client.get("http://localhost:8000/api/search", params=params, timeout=10)
            if resp.status_code != 200:
                await message.answer("Сервер недоступен. Попробуй позже.")
                return
            results = resp.json()
    except Exception:
        await message.answer("Не удалось выполнить поиск. Попробуй позже.")
        return

    if not results:
        query = f"{brand} {model_name}" if model_name else brand
        await message.answer(f"Ничего не найдено по запросу: {query}")
        return

    query = f"{brand} {model_name}" if model_name else brand
    lines = [f"🔍 <b>Результаты: {query}</b> ({len(results)} шт)\n"]
    for i, r in enumerate(results[:5], 1):
        diff = r.get("diff_pct")
        diff_str = f"  📉 {diff:+.0f}%" if diff else ""
        score = r.get("deal_score")
        score_str = f"  ⭐{score:.0f}" if score else ""
        mil = r.get("mileage")
        mil_str = f"  🛣{mil:,}км" if mil else ""
        lines.append(f"{i}. <b>{r['brand']} {r['model']}</b> {r['year']}")
        lines.append(f"   💰 {r['price']:,.0f}₽{diff_str}{score_str}{mil_str}")
        url = r.get("url", "")
        if url:
            lines.append(f"   🔗 {url}")
        lines.append("")

    await message.answer("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)


@router.message(Command("calc"))
async def cmd_calc(message: Message) -> None:
    """Estimate market price. Usage: /calc Toyota Camry 2020 [mileage]"""
    args = message.text.strip().split()
    if len(args) < 4:
        await message.answer(
            "Использование: /calc <марка> <модель> <год> [пробег]\nПример: /calc Toyota Camry 2020 100000"
        )
        return

    brand = args[1]
    model_name = args[2]
    try:
        year = int(args[3])
    except ValueError:
        await message.answer("Год должен быть числом. Пример: /calc Toyota Camry 2020")
        return

    mileage = 0
    if len(args) > 4:
        with contextlib.suppress(ValueError):
            mileage = int(args[4])

    try:
        import httpx

        params = {"brand": brand, "model": model_name, "year": year, "mileage": mileage}
        async with httpx.AsyncClient() as client:
            resp = await client.get("http://localhost:8000/api/price-calculator", params=params, timeout=10)
            if resp.status_code != 200:
                await message.answer("Ошибка оценки. Попробуй позже.")
                return
            data = resp.json()
    except Exception:
        await message.answer("Не удалось оценить цену. Попробуй позже.")
        return

    if "error" in data:
        await message.answer(f"Ошибка: {data['error']}")
        return

    est = data.get("estimated_price", 0)
    low = data.get("price_range", {}).get("low", 0)
    high = data.get("price_range", {}).get("high", 0)
    mil_str = f"\n🛣 Пробег: {mileage:,} км" if mileage else ""

    await message.answer(
        f"💰 <b>Оценка: {brand} {model_name} {year}</b>{mil_str}\n\n"
        f"📊 Рыночная цена: <b>{est:,.0f} ₽</b>\n"
        f"📉 Минимум (P10): {low:,.0f} ₽\n"
        f"📈 Максимум (P90): {high:,.0f} ₽",
        parse_mode="HTML",
    )
