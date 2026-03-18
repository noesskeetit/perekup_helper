"""REST-App.net API client for fetching Avito auto listings."""

import json
import logging
import os
import re
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

RESTAPP_URL = "https://rest-app.net/api/ads"
CATEGORY_AUTO = "9"


def get_credentials():
    login = os.environ.get("RESTAPP_LOGIN", "")
    token = os.environ.get("RESTAPP_TOKEN", "")
    return login, token


def fetch_listings(last_minutes=30, limit=50, region_id=None, city_id=None, price_from=None, price_to=None, query=None):
    """Fetch auto listings from REST-App.net API.

    Returns list of normalized listing dicts ready for upsert_listing().
    """
    login, token = get_credentials()
    if not login or not token:
        logger.error("RESTAPP_LOGIN and RESTAPP_TOKEN must be set")
        return []

    params = {
        "login": login,
        "token": token,
        "category_id": CATEGORY_AUTO,
        "last_m": str(last_minutes),
        "limit": str(limit),
        "format": "json",
        "sort": "desc",
    }

    if region_id:
        params["region_id"] = str(region_id)
    if city_id:
        params["city_id"] = str(city_id)
    if price_from:
        params["price1"] = str(price_from)
    if price_to:
        params["price2"] = str(price_to)
    if query:
        params["q"] = query

    query_string = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    url = f"{RESTAPP_URL}?{query_string}"

    logger.info("Fetching from REST-App.net: last_%dm, limit=%s", last_minutes, limit)

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        logger.error("REST-App.net API error: %s", e)
        return []

    if data.get("status") != "ok":
        logger.error("REST-App.net returned status: %s", data.get("status"))
        return []

    raw_items = data.get("data", [])
    logger.info("REST-App.net returned %d items", len(raw_items))

    return [_normalize_item(item) for item in raw_items]


def _normalize_item(item):
    """Convert REST-App.net item to our listing format."""
    params = {p["name"]: p["value"] for p in item.get("params", []) if "name" in p and "value" in p}

    # Extract brand and model from title (e.g. "Toyota Camry 2.5 AT, 2020")
    title = item.get("title", "")
    brand, model = _parse_brand_model(title)

    # Year from params or title
    year = _parse_int(params.get("Год выпуска")) or _extract_year(title)

    # Mileage from params
    mileage = _parse_mileage(params.get("Пробег"))

    # Images
    images_str = item.get("images", "")
    photos = [url.strip() for url in images_str.split(",") if url.strip()] if images_str else None

    return {
        "source": "avito",
        "external_id": item.get("avito_id") or item.get("Id", ""),
        "brand": brand,
        "model": model,
        "year": year or 0,
        "mileage": mileage,
        "price": _parse_int(item.get("price")) or 0,
        "market_price": None,  # REST-App.net doesn't provide market price
        "price_diff_pct": None,
        "description": item.get("description", ""),
        "url": item.get("url", ""),
        "photos": photos,
        "raw_data": {
            "title": title,
            "params": params,
            "city": item.get("city"),
            "region": item.get("region"),
            "phone": item.get("phone"),
            "name": item.get("name"),
            "address": item.get("address"),
        },
    }


def _parse_brand_model(title):
    """Extract brand and model from title like 'Toyota Camry 2.5 AT, 2020'."""
    if not title:
        return "", ""
    # Remove year suffix
    parts = title.split(",")[0].strip().split()
    if len(parts) >= 2:
        return parts[0], parts[1]
    elif len(parts) == 1:
        return parts[0], ""
    return "", ""


def _extract_year(title):
    """Extract year from title like '... , 2020'."""
    m = re.search(r",\s*(20\d{2})", title)
    return int(m.group(1)) if m else None


def _parse_int(value):
    if value is None:
        return None
    try:
        return int(str(value).replace(" ", "").replace("\xa0", ""))
    except (ValueError, TypeError):
        return None


def _parse_mileage(value):
    """Parse mileage like '45 000 км' to integer."""
    if not value:
        return None
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) if digits else None
