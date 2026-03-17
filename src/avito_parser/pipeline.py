"""Main scraping pipeline orchestrating listing and card parsing."""

import json
import logging

from .card_parser import parse_card_page
from .config import settings
from .http_client import AvitoHttpClient
from .listing_parser import (
    ListingItem,
    SearchFilters,
    build_search_url,
    has_next_page,
    parse_listing_page,
)
from .models import get_session_factory, upsert_car_ad
from .price_analyzer import calculate_price_deviation

logger = logging.getLogger(__name__)


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


async def scrape_and_save(filters: SearchFilters) -> int:
    """Full pipeline: scrape listings, parse cards, save to DB."""
    client = AvitoHttpClient()
    session_factory = get_session_factory()
    saved_count = 0

    try:
        items = await scrape_listings(filters, client)
        logger.info("Total listing items found: %d", len(items))

        session = session_factory()
        try:
            for item in items:
                card_data = await _process_card(client, item)
                if card_data:
                    upsert_car_ad(session, card_data)
                    saved_count += 1

            session.commit()
            logger.info("Saved/updated %d car ads", saved_count)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    finally:
        await client.close()

    return saved_count


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
