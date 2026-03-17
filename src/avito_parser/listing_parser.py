"""Parse Avito auto listing pages to extract ad links and basic info."""

import json
import logging
import re
from dataclasses import dataclass
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from .config import settings

logger = logging.getLogger(__name__)


@dataclass
class ListingItem:
    external_id: str
    url: str
    title: str = ""
    price: int | None = None


@dataclass
class SearchFilters:
    brand: str | None = None
    model: str | None = None
    year_from: int | None = None
    year_to: int | None = None
    price_from: int | None = None
    price_to: int | None = None
    location_slug: str = "rossiya"  # "moskva", "sankt-peterburg", etc.


def build_search_url(filters: SearchFilters, page: int = 1) -> str:
    """Build Avito search URL for auto category with given filters."""
    parts = [settings.avito_base_url, filters.location_slug, "avtomobili"]

    if filters.brand:
        parts.append(filters.brand.lower())
        if filters.model:
            parts.append(filters.model.lower())

    base = "/".join(parts)

    params: dict[str, str] = {}
    if filters.price_from is not None:
        params["pmin"] = str(filters.price_from)
    if filters.price_to is not None:
        params["pmax"] = str(filters.price_to)
    if filters.year_from is not None:
        params["params[110000_from]"] = str(filters.year_from)
    if filters.year_to is not None:
        params["params[110000_to]"] = str(filters.year_to)
    if page > 1:
        params["p"] = str(page)

    if params:
        return f"{base}?{urlencode(params)}"
    return base


def _extract_from_json_ld(soup: BeautifulSoup) -> list[ListingItem]:
    """Try to extract listing items from JSON-LD or embedded JSON data."""
    items = []

    for script_tag in soup.find_all("script", type="application/json"):
        try:
            data = json.loads(script_tag.string or "")
            if isinstance(data, dict):
                items.extend(_walk_json_for_items(data))
        except (json.JSONDecodeError, TypeError):
            continue

    return items


def _walk_json_for_items(data: dict, depth: int = 0) -> list[ListingItem]:
    """Recursively walk JSON data looking for ad-like structures."""
    items = []
    if depth > 10:
        return items

    if "id" in data and "title" in data and "urlPath" in data:
        external_id = str(data["id"])
        url = settings.avito_base_url + data["urlPath"]
        title = data.get("title", "")
        price_raw = data.get("price", data.get("priceDetailed", {}).get("value"))
        price = _parse_price(price_raw)
        items.append(ListingItem(external_id=external_id, url=url, title=title, price=price))

    for value in data.values():
        if isinstance(value, dict):
            items.extend(_walk_json_for_items(value, depth + 1))
        elif isinstance(value, list):
            for v in value:
                if isinstance(v, dict):
                    items.extend(_walk_json_for_items(v, depth + 1))
    return items


def _parse_price(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        digits = re.sub(r"[^\d]", "", value)
        return int(digits) if digits else None
    return None


def parse_listing_page(html: str) -> list[ListingItem]:
    """Parse a listing page HTML and return list of ad items."""
    soup = BeautifulSoup(html, "lxml")
    items: list[ListingItem] = []

    # Strategy 1: JSON embedded data
    items = _extract_from_json_ld(soup)
    if items:
        logger.info("Extracted %d items from JSON data", len(items))
        return items

    # Strategy 2: HTML parsing via data-marker attributes
    ad_cards = soup.find_all("div", attrs={"data-marker": "item"})
    if not ad_cards:
        ad_cards = soup.find_all("div", attrs={"itemtype": "http://schema.org/Product"})

    for card in ad_cards:
        try:
            link = card.find("a", attrs={"data-marker": "item-title"})
            if not link:
                link = card.find("a", href=True)
            if not link:
                continue

            href = link.get("href", "")
            external_id = _extract_id_from_url(href)
            if not external_id:
                continue

            url = href if href.startswith("http") else settings.avito_base_url + href
            title = link.get_text(strip=True)

            price_el = card.find("meta", attrs={"itemprop": "price"})
            price = None
            if price_el:
                price = _parse_price(price_el.get("content"))
            else:
                price_span = card.find("span", attrs={"data-marker": "item-price"})
                if price_span:
                    price = _parse_price(price_span.get_text())

            items.append(ListingItem(external_id=external_id, url=url, title=title, price=price))
        except Exception as e:
            logger.warning("Failed to parse card: %s", e)
            continue

    logger.info("Extracted %d items from HTML", len(items))
    return items


def _extract_id_from_url(url: str) -> str | None:
    """Extract numeric Avito ad ID from URL like /path/to/ad_12345."""
    match = re.search(r"_(\d+)$", url.split("?")[0])
    if match:
        return match.group(1)
    match = re.search(r"/(\d+)$", url.split("?")[0])
    if match:
        return match.group(1)
    return None


def has_next_page(html: str) -> bool:
    """Check if listing page has a next page link."""
    soup = BeautifulSoup(html, "lxml")
    next_btn = soup.find("a", attrs={"data-marker": "pagination-button/nextPage"})
    if next_btn:
        return True
    next_link = soup.find("a", attrs={"data-marker": "pagination-button/next"})
    return next_link is not None
