"""Background task that periodically checks for new listings and notifies users."""

import asyncio
import logging

from aiogram import Bot
from sqlalchemy import select

from bot.config import settings
from bot.db.models import Filter, NotificationLog, User
from bot.db.session import async_session
from bot.services.checker import DatabaseChecker, DemoChecker, Listing, ListingChecker

logger = logging.getLogger(__name__)


def _matches(listing: Listing, f: Filter) -> bool:
    if f.brand and listing.brand.lower() != f.brand.lower():
        return False
    if f.model and listing.model.lower() != f.model.lower():
        return False
    if f.max_price is not None and listing.price > f.max_price:
        return False
    return not (f.min_discount is not None and listing.discount_pct < f.min_discount)


def _format_message(listing: Listing) -> str:
    return (
        f"🚗 {listing.brand} {listing.model} {listing.year}\n"
        f"💰 Цена: {listing.price:,.0f} ₽\n"
        f"📈 Рыночная: {listing.market_price:,.0f} ₽\n"
        f"🔥 Дисконт: {listing.discount_pct}%\n"
        f"📦 Категория: {listing.category}\n"
        f"🔗 {listing.url}"
    )


async def _notify_user(
    bot: Bot,
    telegram_id: int,
    listing: Listing,
) -> None:
    text = _format_message(listing)
    try:
        if listing.photo_url:
            await bot.send_photo(
                chat_id=telegram_id,
                photo=listing.photo_url,
                caption=text,
            )
        else:
            await bot.send_message(chat_id=telegram_id, text=text)
    except Exception:
        logger.exception("Failed to send notification to %s", telegram_id)
        return

    async with async_session() as session:
        session.add(NotificationLog(telegram_id=telegram_id, listing_url=listing.url))
        await session.commit()


async def run_notifier(bot: Bot, checker: ListingChecker | None = None) -> None:
    """Long-running task: fetch listings → match filters → send messages."""
    if checker is None:
        checker = DatabaseChecker(settings.app_database_url) if settings.app_database_url else DemoChecker()

    while True:
        try:
            listings = await checker.fetch_new()
            if not listings:
                logger.debug("No new listings found")
                await asyncio.sleep(settings.check_interval_seconds)
                continue

            async with async_session() as session:
                result = await session.execute(select(User).where(User.is_active.is_(True)))
                users = result.scalars().all()

                for user in users:
                    filters_result = await session.execute(select(Filter).where(Filter.telegram_id == user.telegram_id))
                    user_filters = filters_result.scalars().all()
                    if not user_filters:
                        continue

                    # Pre-fetch already-sent URLs for this user to avoid duplicates.
                    sent_result = await session.execute(
                        select(NotificationLog.listing_url).where(NotificationLog.telegram_id == user.telegram_id)
                    )
                    sent_urls: set[str] = {row[0] for row in sent_result}

                    for listing in listings:
                        if listing.url in sent_urls:
                            continue
                        for f in user_filters:
                            if _matches(listing, f):
                                await _notify_user(bot, user.telegram_id, listing)
                                sent_urls.add(listing.url)
                                break  # one notification per listing per user

        except asyncio.CancelledError:
            logger.info("Notifier task cancelled")
            return
        except Exception:
            logger.exception("Error in notifier loop")

        await asyncio.sleep(settings.check_interval_seconds)
