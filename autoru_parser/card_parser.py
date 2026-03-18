"""Parse individual auto.ru car ad page to extract all parameters."""

import contextlib
import json
import logging
import re

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

PARAM_MAPPING = {
    "Марка": "brand",
    "Модель": "model",
    "Год выпуска": "year",
    "Год": "year",
    "Пробег": "mileage_km",
    "Тип двигателя": "engine_type",
    "Двигатель": "engine_type",
    "Объём двигателя": "engine_volume",
    "Объем двигателя": "engine_volume",
    "Мощность": "engine_power_hp",
    "Коробка передач": "transmission",
    "КПП": "transmission",
    "Привод": "drive_type",
    "Тип кузова": "body_type",
    "Кузов": "body_type",
    "Цвет": "color",
    "Руль": "steering_wheel",
    "VIN": "vin",
}


def parse_card_page(html: str, url: str = "") -> dict:
    """Parse an individual auto.ru ad page and return structured data dict."""
    soup = BeautifulSoup(html, "lxml")
    data: dict = {}

    # Strategy 1: JSON-LD structured data
    json_ld = _extract_json_ld(soup)
    if json_ld:
        data.update(_parse_json_ld_card(json_ld))

    # Strategy 2: Embedded JSON state (auto.ru uses React SSR)
    embedded = _extract_embedded_state(html, soup)
    if embedded:
        data.update(_merge_if_missing(data, _parse_embedded_offer(embedded)))

    # Strategy 3: HTML parsing fallback
    html_data = _parse_html_card(soup)
    data.update(_merge_if_missing(data, html_data))

    # Market/estimated price
    market_data = _extract_market_price(html)
    data.update(_merge_if_missing(data, market_data))

    # VIN
    if not data.get("vin"):
        data["vin"] = _extract_vin(soup, html)

    # Photos
    if not data.get("photo_urls"):
        data["photo_urls"] = json.dumps(_extract_photos(soup, html))

    # External ID from URL
    if not data.get("external_id") and url:
        eid = _extract_id_from_url(url)
        if eid:
            data["external_id"] = eid

    data.setdefault("url", url)
    return data


def _extract_json_ld(soup: BeautifulSoup) -> dict | None:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            ld = json.loads(script.string or "")
            if isinstance(ld, dict) and ld.get("@type") in ("Product", "Car", "Vehicle"):
                return ld
            if isinstance(ld, list):
                for item in ld:
                    if isinstance(item, dict) and item.get("@type") in ("Product", "Car", "Vehicle"):
                        return item
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def _parse_json_ld_card(ld: dict) -> dict:
    data: dict = {}
    data["title"] = ld.get("name", "")
    data["description"] = ld.get("description", "")

    offers = ld.get("offers", {})
    if isinstance(offers, dict):
        price = offers.get("price")
        if price:
            with contextlib.suppress(ValueError, TypeError):
                data["price"] = int(float(price))

    if "image" in ld:
        images = ld["image"]
        if isinstance(images, str):
            images = [images]
        data["photo_urls"] = json.dumps(images if isinstance(images, list) else [])

    if "vehicleIdentificationNumber" in ld:
        data["vin"] = ld["vehicleIdentificationNumber"]

    return data


def _extract_embedded_state(html: str, soup: BeautifulSoup) -> dict | None:
    """Extract auto.ru initial state JSON containing offer/card data."""
    # Try <script id="initial-state">
    script_tag = soup.find("script", id="initial-state")
    if script_tag:
        try:
            return json.loads(script_tag.string or "")
        except (json.JSONDecodeError, TypeError):
            pass

    # Try window.__initialState__ = {...}
    ws_pattern = re.compile(r'window\.__initialState__\s*=\s*(\{.*?\})\s*;', re.DOTALL)
    match = ws_pattern.search(html)
    if match:
        try:
            return json.loads(match.group(1))
        except (json.JSONDecodeError, TypeError):
            pass

    # Try any application/json script
    for script in soup.find_all("script", type="application/json"):
        try:
            content = json.loads(script.string or "")
            if isinstance(content, dict):
                return content
        except (json.JSONDecodeError, TypeError):
            continue

    return None


def _parse_embedded_offer(data: dict, depth: int = 0) -> dict:
    """Walk embedded JSON to find and parse an auto.ru offer structure."""
    result: dict = {}
    if depth > 15:
        return result

    if not isinstance(data, dict):
        return result

    # auto.ru offer: has vehicle_info + price_info
    vehicle = data.get("vehicle_info") or data.get("vehicleInfo")
    price_info = data.get("price_info") or data.get("priceInfo")

    if vehicle and isinstance(vehicle, dict):
        mark = vehicle.get("mark", {})
        model = vehicle.get("model", {})
        if isinstance(mark, dict):
            result["brand"] = mark.get("name", "")
        if isinstance(model, dict):
            result["model"] = model.get("name", "")

        tech = vehicle.get("tech_param") or vehicle.get("techParam") or {}
        if isinstance(tech, dict):
            if tech.get("year"):
                result["year"] = int(tech["year"])
            if tech.get("engine_volume"):
                with contextlib.suppress(ValueError, TypeError):
                    result["engine_volume"] = float(tech["engine_volume"]) / 1000
            if tech.get("engine_power"):
                with contextlib.suppress(ValueError, TypeError):
                    result["engine_power_hp"] = int(tech["engine_power"])
            if tech.get("transmission"):
                result["transmission"] = str(tech["transmission"])
            if tech.get("drive"):
                result["drive_type"] = str(tech["drive"])

        body_type = vehicle.get("body_type") or vehicle.get("bodyType")
        if body_type:
            result["body_type"] = str(body_type)

        color = vehicle.get("color", {})
        if isinstance(color, dict):
            result["color"] = color.get("name", "")
        elif isinstance(color, str):
            result["color"] = color

    if price_info and isinstance(price_info, dict):
        price = price_info.get("price") or price_info.get("RUR")
        if price:
            with contextlib.suppress(ValueError, TypeError):
                result["price"] = int(float(price))

    state = data.get("state", {})
    if isinstance(state, dict) and state.get("mileage"):
        with contextlib.suppress(ValueError, TypeError):
            result["mileage_km"] = int(state["mileage"])

    if data.get("description"):
        result["description"] = str(data["description"])

    if data.get("id") or data.get("saleId"):
        result["external_id"] = str(data.get("saleId") or data.get("id"))

    seller = data.get("seller", {})
    if isinstance(seller, dict):
        result["seller_name"] = seller.get("name", "")
        location = seller.get("location", {})
        if isinstance(location, dict):
            result["location"] = location.get("city", "") or location.get("region_info", {}).get("name", "")

    docs = data.get("documents", {})
    if isinstance(docs, dict) and docs.get("vin"):
        result["vin"] = str(docs["vin"])

    # If we found something meaningful, return; otherwise recurse
    if result.get("brand") or result.get("price"):
        return result

    # Recurse
    for val in data.values():
        if isinstance(val, dict):
            sub = _parse_embedded_offer(val, depth + 1)
            if sub.get("brand") or sub.get("price"):
                return sub
        elif isinstance(val, list):
            for v in val:
                if isinstance(v, dict):
                    sub = _parse_embedded_offer(v, depth + 1)
                    if sub.get("brand") or sub.get("price"):
                        return sub

    return result


def _parse_html_card(soup: BeautifulSoup) -> dict:
    """Parse ad parameters from auto.ru HTML elements."""
    data: dict = {}

    # Title
    title_el = soup.find("h1", class_=re.compile(r"CardTitle|OfferTitle"))
    if not title_el:
        title_el = soup.find("h1")
    if title_el:
        data["title"] = title_el.get_text(strip=True)

    # Price
    price_el = soup.find(class_=re.compile(r"OfferPrice|CardPrice|price"))
    if price_el:
        price_text = price_el.get_text()
        digits = re.sub(r"[^\d]", "", price_text)
        if digits:
            data["price"] = int(digits)

    # Description
    desc_el = soup.find(class_=re.compile(r"CardDescription|OfferDescription|description"))
    if desc_el:
        data["description"] = desc_el.get_text(strip=True)

    # Parameter list items: "Label: Value"
    for li in soup.find_all("li"):
        text = li.get_text(strip=True)
        for label, field_name in PARAM_MAPPING.items():
            if label.lower() in text.lower():
                parts = text.split(":")
                if len(parts) >= 2:
                    value_part = ":".join(parts[1:]).strip()
                    _apply_param_value(data, field_name, value_part)
                break

    # Seller
    seller_el = soup.find(class_=re.compile(r"SellerName|seller.*name", re.IGNORECASE))
    if seller_el:
        data["seller_name"] = seller_el.get_text(strip=True)

    # Location
    location_el = soup.find(class_=re.compile(r"MetroList|SellerLocation|seller.*location", re.IGNORECASE))
    if location_el:
        data["location"] = location_el.get_text(strip=True)

    return data


def _apply_param_value(data: dict, field_name: str, value: str):
    if field_name in ("year", "engine_power_hp", "mileage_km"):
        digits = re.sub(r"[^\d]", "", value)
        if digits:
            data[field_name] = int(digits)
    elif field_name == "engine_volume":
        match = re.search(r"(\d+[.,]?\d*)", value)
        if match:
            data[field_name] = float(match.group(1).replace(",", "."))
    else:
        data[field_name] = value


def _extract_market_price(html: str) -> dict:
    """Extract market/estimated price from auto.ru page."""
    data: dict = {}

    for pattern_str in (r'"market_price"\s*:\s*(\d+)', r'"marketPrice"\s*:\s*(\d+)', r'"estimated_price"\s*:\s*(\d+)'):
        match = re.search(pattern_str, html)
        if match:
            data["market_price"] = int(match.group(1))
            return data

    return data


def _extract_vin(soup: BeautifulSoup, html: str) -> str | None:
    """Extract VIN number from auto.ru page."""
    vin_pattern = re.compile(r'"vin"\s*:\s*"([A-HJ-NPR-Z0-9]{17})"', re.IGNORECASE)
    match = vin_pattern.search(html)
    if match:
        return match.group(1).upper()

    # HTML text search
    for el in soup.find_all(string=re.compile(r"VIN", re.IGNORECASE)):
        parent = el.parent
        if parent:
            full_text = parent.get_text()
            match = re.search(r"[A-HJ-NPR-Z0-9]{17}", full_text, re.IGNORECASE)
            if match:
                return match.group(0).upper()

    return None


def _extract_photos(soup: BeautifulSoup, html: str) -> list[str]:
    """Extract photo URLs from auto.ru ad page."""
    photos: list[str] = []

    # auto.ru uses Yandex image CDN
    img_patterns = [
        re.compile(r'"(https://[^"]*(?:avatars\.mds\.yandex\.net|img\.autoru\.yandex)[^"]*(?:1200x900|orig|full)[^"]*)"'),
        re.compile(r'"(https://[^"]*avatars\.mds\.yandex\.net[^"]*)"'),
    ]
    for pattern in img_patterns:
        for match in pattern.finditer(html):
            url = match.group(1)
            if url not in photos:
                photos.append(url)
        if photos:
            return photos

    # og:image meta tags
    for meta in soup.find_all("meta", property="og:image"):
        url = meta.get("content", "")
        if url and url not in photos:
            photos.append(url)

    return photos


def _extract_id_from_url(url: str) -> str | None:
    """Extract sale ID from auto.ru URL."""
    path = url.split("?")[0].rstrip("/")
    match = re.search(r"/(\d{7,}-[0-9a-f]+)/?$", path, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"/(\d{7,})/?$", path)
    if match:
        return match.group(1)
    return None


def _merge_if_missing(base: dict, extra: dict) -> dict:
    """Return dict of extra items that are missing from base."""
    result = {}
    for key, value in extra.items():
        if key not in base or base[key] is None:
            result[key] = value
    return result
