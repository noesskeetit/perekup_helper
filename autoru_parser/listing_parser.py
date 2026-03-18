"""Parse auto.ru listing pages to extract ad links and basic info."""

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
    location_slug: str = ""  # unused for auto.ru but kept for interface compat


def build_search_url(filters: SearchFilters, page: int = 1) -> str:
    """Build auto.ru search URL for used cars with given filters."""
    base = f"{settings.autoru_base_url}/cars/used/list/"

    params: dict[str, str] = {}
    if filters.brand:
        params["mark"] = filters.brand.upper()
    if filters.model:
        params["model"] = filters.model.upper()
    if filters.price_from is not None:
        params["price_from"] = str(filters.price_from)
    if filters.price_to is not None:
        params["price_to"] = str(filters.price_to)
    if filters.year_from is not None:
        params["year_from"] = str(filters.year_from)
    if filters.year_to is not None:
        params["year_to"] = str(filters.year_to)
    if page > 1:
        params["page"] = str(page)

    if params:
        return f"{base}?{urlencode(params)}"
    return base


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


def _extract_from_initial_state(html: str) -> list[ListingItem]:
    """Extract listing items from auto.ru embedded state JSON."""
    items: list[ListingItem] = []

    # Try <script id="initial-state"> tag
    pattern = re.compile(r'<script[^>]+id=["\']initial-state["\'][^>]*>(.*?)</script>', re.DOTALL)
    match = pattern.search(html)
    if match:
        try:
            data = json.loads(match.group(1))
            items = _walk_offers(data)
            if items:
                return items
        except (json.JSONDecodeError, TypeError):
            pass

    # Try window.__initialState__ = {...}
    ws_pattern = re.compile(r'window\.__initialState__\s*=\s*(\{.*?\})\s*;', re.DOTALL)
    match = ws_pattern.search(html)
    if match:
        try:
            data = json.loads(match.group(1))
            items = _walk_offers(data)
            if items:
                return items
        except (json.JSONDecodeError, TypeError):
            pass

    # Try any application/json script tags
    soup = BeautifulSoup(html, "lxml")
    for script in soup.find_all("script", type="application/json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, dict):
                items = _walk_offers(data)
                if items:
                    return items
        except (json.JSONDecodeError, TypeError):
            continue

    return items


def _walk_offers(data: dict, depth: int = 0) -> list[ListingItem]:
    """Recursively walk JSON data looking for auto.ru offer structures."""
    items: list[ListingItem] = []
    if depth > 12:
        return items

    # auto.ru offer structure: has "saleId" or "id" + "vehicle_info"
    if isinstance(data, dict):
        sale_id = data.get("saleId") or data.get("id")
        vehicle = data.get("vehicle_info") or data.get("vehicleInfo")
        price_info = data.get("price_info") or data.get("priceInfo")

        if sale_id and vehicle and price_info:
            external_id = str(sale_id)
            url = data.get("url", "")
            if not url:
                url = f"{settings.autoru_base_url}/cars/used/sale/{external_id}/"
            elif url.startswith("//"):
                url = "https:" + url

            mark = vehicle.get("mark", {})
            model = vehicle.get("model", {})
            brand_name = mark.get("name", "") if isinstance(mark, dict) else str(mark)
            model_name = model.get("name", "") if isinstance(model, dict) else str(model)
            title = f"{brand_name} {model_name}".strip()

            price = None
            if isinstance(price_info, dict):
                price = _parse_price(price_info.get("price") or price_info.get("RUR"))

            items.append(ListingItem(external_id=external_id, url=url, title=title, price=price))
            return items

        # Walk list of offers
        if "offers" in data and isinstance(data["offers"], list):
            for offer in data["offers"]:
                if isinstance(offer, dict):
                    sub = _walk_offers(offer, depth + 1)
                    items.extend(sub)
            if items:
                return items

        # Recurse into nested dicts
        for val in data.values():
            if isinstance(val, dict):
                items.extend(_walk_offers(val, depth + 1))
            elif isinstance(val, list):
                for v in val:
                    if isinstance(v, dict):
                        items.extend(_walk_offers(v, depth + 1))

    return items


def parse_listing_page(html: str) -> list[ListingItem]:
    """Parse a listing page HTML and return list of ad items."""
    # Strategy 1: embedded JSON state
    items = _extract_from_initial_state(html)
    if items:
        logger.info("Extracted %d items from embedded JSON", len(items))
        return items

    # Strategy 2: HTML parsing
    soup = BeautifulSoup(html, "lxml")
    items = _parse_html_listing(soup)
    if items:
        logger.info("Extracted %d items from HTML", len(items))

    return items


def _parse_html_listing(soup: BeautifulSoup) -> list[ListingItem]:
    """Parse listing items from HTML elements."""
    items: list[ListingItem] = []

    # auto.ru listing items: <a class="ListingItemTitle__link"> or <article class="ListingItem">
    for card in soup.find_all("article", class_=re.compile(r"ListingItem")):
        try:
            link = card.find("a", class_=re.compile(r"ListingItemTitle"))
            if not link:
                link = card.find("a", href=re.compile(r"/cars/used/sale/"))
            if not link:
                continue

            href = link.get("href", "")
            if not href:
                continue

            url = href if href.startswith("http") else settings.autoru_base_url + href
            external_id = _extract_id_from_url(url)
            if not external_id:
                continue

            title = link.get_text(strip=True)

            price = None
            price_el = card.find(class_=re.compile(r"Price"))
            if price_el:
                price = _parse_price(price_el.get_text())

            items.append(ListingItem(external_id=external_id, url=url, title=title, price=price))
        except Exception as e:
            logger.warning("Failed to parse listing card: %s", e)
            continue

    return items


def _extract_id_from_url(url: str) -> str | None:
    """Extract auto.ru sale ID from URL like /cars/used/sale/brand/model/1234567890-abc/"""
    # Pattern: digits-hexhash at end of path
    path = url.split("?")[0].rstrip("/")
    match = re.search(r"/(\d{7,}-[0-9a-f]+)/?$", path, re.IGNORECASE)
    if match:
        return match.group(1)
    # Fallback: just numeric ID
    match = re.search(r"/(\d{7,})/?$", path)
    if match:
        return match.group(1)
    return None


def has_next_page(html: str) -> bool:
    """Check if listing page has a next page link."""
    soup = BeautifulSoup(html, "lxml")

    # auto.ru next page button
    next_btn = soup.find("a", class_=re.compile(r"ListingPagination.*next|Pager.*next", re.IGNORECASE))
    if next_btn:
        return True

    # Generic next rel link
    next_link = soup.find("link", rel="next")
    if next_link:
        return True

    # Check for next page in JSON state
    return '"hasNextPage":true' in html or '"has_next_page":true' in html
