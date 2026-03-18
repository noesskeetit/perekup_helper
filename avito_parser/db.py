"""DB integration: save parsed Avito listings to the app's listings table."""

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.listing import Listing

logger = logging.getLogger(__name__)


def map_card_to_listing(card_data: dict[str, Any]) -> dict[str, Any]:
    """Map avito_parser card dict fields to Listing model fields."""
    photo_urls = card_data.get("photo_urls")
    photos: list[str] | None = None
    if isinstance(photo_urls, str):
        try:
            photos = json.loads(photo_urls)
        except (json.JSONDecodeError, ValueError):
            photos = None
    elif isinstance(photo_urls, list):
        photos = photo_urls

    return {
        "source": "avito",
        "external_id": card_data.get("external_id", ""),
        "brand": card_data.get("brand") or "",
        "model": card_data.get("model") or "",
        "year": card_data.get("year") or 0,
        "mileage": card_data.get("mileage_km"),
        "price": card_data.get("price") or 0,
        "market_price": card_data.get("market_price"),
        "price_diff_pct": card_data.get("price_deviation_pct"),
        "description": card_data.get("description"),
        "url": card_data.get("url", ""),
        "photos": photos,
        "raw_data": {k: v for k, v in card_data.items() if v is not None},
    }


async def upsert_listing(session: AsyncSession, card_data: dict[str, Any]) -> Listing:
    """Insert or update a Listing by (source='avito', external_id).

    If a record with the same external_id already exists, non-None fields
    are updated. Otherwise a new Listing is inserted.
    """
    external_id = card_data.get("external_id")
    if not external_id:
        raise ValueError("external_id is required")

    fields = map_card_to_listing(card_data)

    result = await session.execute(
        select(Listing).where(
            Listing.source == "avito",
            Listing.external_id == external_id,
        )
    )
    existing = result.scalar_one_or_none()

    if existing is not None:
        for key, value in fields.items():
            if value is not None:
                setattr(existing, key, value)
        logger.debug("Updated listing external_id=%s", external_id)
        return existing

    listing = Listing(**fields)
    session.add(listing)
    logger.debug("Inserted listing external_id=%s", external_id)
    return listing
