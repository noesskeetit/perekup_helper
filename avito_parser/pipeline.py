"""Main scraping pipeline orchestrating listing and card parsing."""

import json
import logging
from dataclasses import dataclass, field

from app.db.session import async_session_factory

from .analysis import analyze_and_save
from .card_parser import parse_card_page
from .config import settings
from .db import upsert_listing
from .http_client import AvitoHttpClient
from .listing_parser import (
    ListingItem,
    SearchFilters,
    build_search_url,
    has_next_page,
    parse_listing_page,
)
from .price_analyzer import calculate_price_deviation

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Counts from a single pipeline run."""

    new: int = field(default=0)
    updated: int = field(default=0)
    analyzed: int = field(default=0)

    @property
    def total(self) -> int:
        return self.new + self.updated


async def scrape_listings(filters: SearchFilters, client: AvitoHttpClient) -> list[ListingItem]:
    """Scrape all listing pages for given filters."""
    all_items: list[ListingItem] = []

    for page in range(1, settings.max_pages + 1):
        url = build_search_url(filters, page)
        logger.info("Fetching listing page %d: %s", page, url)

        html = await client.get(url)
        if not html:
            logger.warning("Failed to fetch page %d, stopping", page)
            break

        items = parse_listing_page(html)
        if not items:
            logger.info("No items on page %d, stopping", page)
            break

        all_items.extend(items)
        logger.info("Page %d: found %d items (total: %d)", page, len(items), len(all_items))

        if not has_next_page(html):
            logger.info("No next page, stopping at page %d", page)
            break

    return all_items


async def scrape_and_save(filters: SearchFilters) -> PipelineResult:
    """Full pipeline: scrape listings, parse cards, save to DB."""
    client = AvitoHttpClient()
    result = PipelineResult()

    try:
        items = await scrape_listings(filters, client)
        logger.info("Total listing items found: %d", len(items))

        async with async_session_factory() as session:
            try:
                for item in items:
                    card_data = await _process_card(client, item)
                    if card_data:
                        listing, is_new = await upsert_listing(session, card_data)
                        if is_new:
                            result.new += 1
                        else:
                            result.updated += 1
                        analysis = await analyze_and_save(session, listing)
                        if analysis is not None:
                            result.analyzed += 1

                await session.commit()
                logger.info(
                    "Pipeline complete: new=%d, updated=%d, analyzed=%d",
                    result.new,
                    result.updated,
                    result.analyzed,
                )
            except Exception:
                await session.rollback()
                raise
    finally:
        await client.close()

    return result


async def _process_card(client: AvitoHttpClient, item: ListingItem) -> dict | None:
    """Fetch and parse a single card page."""
    logger.info("Fetching card: %s", item.url)
    html = await client.get(item.url)
    if not html:
        logger.warning("Failed to fetch card %s", item.external_id)
        return None

    try:
        card_data = parse_card_page(html, item.url)
    except Exception as e:
        logger.error("Failed to parse card %s: %s", item.external_id, e)
        return None

    # Ensure essential fields from listing
    card_data.setdefault("external_id", item.external_id)
    card_data.setdefault("url", item.url)
    card_data.setdefault("title", item.title)
    if item.price and not card_data.get("price"):
        card_data["price"] = item.price

    # Calculate price deviation
    price = card_data.get("price")
    market_price = card_data.get("market_price")
    deviation = calculate_price_deviation(price, market_price)
    if deviation is not None:
        card_data["price_deviation_pct"] = deviation

    # Ensure photo_urls is JSON string
    if "photo_urls" in card_data and isinstance(card_data["photo_urls"], list):
        card_data["photo_urls"] = json.dumps(card_data["photo_urls"])

    return card_data
