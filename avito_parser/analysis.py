"""AI-анализ объявлений после парсинга: категоризация и сохранение в listing_analysis."""

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.listing import Listing, ListingAnalysis
from perekup_helper.categorizer import Categorizer
from perekup_helper.models import ListingDescription

logger = logging.getLogger(__name__)


async def analyze_and_save(session: AsyncSession, listing: Listing) -> ListingAnalysis | None:
    """Запустить AI-категоризацию для объявления и сохранить результат.

    Пропускает объявления, у которых анализ уже есть.
    При ошибке категоризации логирует и возвращает None (не падает весь pipeline).
    """
    existing = await session.scalar(
        select(ListingAnalysis).where(ListingAnalysis.listing_id == listing.id)
    )
    if existing is not None:
        logger.debug("Анализ уже существует для listing %s, пропускаем", listing.id)
        return None

    text = listing.description or f"{listing.brand} {listing.model} {listing.year}"
    ld = ListingDescription(
        id=str(listing.id),
        text=text,
        price=listing.price,
        market_price=listing.market_price,
    )

    try:
        categorizer = Categorizer()
        score_result = await asyncio.to_thread(categorizer.categorize_and_score, ld)
    except Exception:
        logger.exception("Ошибка AI-категоризации для listing %s", listing.id)
        return None

    analysis = ListingAnalysis(
        id=uuid.uuid4(),
        listing_id=listing.id,
        category=score_result.category_result.category.value,
        confidence=score_result.category_result.confidence,
        ai_summary=score_result.category_result.reasoning or None,
        flags=score_result.category_result.flags or None,
        score=score_result.attractiveness_score,
    )
    session.add(analysis)
    logger.info(
        "Сохранён анализ для listing %s: category=%s, score=%.1f",
        listing.id,
        analysis.category,
        analysis.score,
    )
    return analysis
