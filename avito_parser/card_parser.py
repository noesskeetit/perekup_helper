"""Parse individual Avito auto ad page to extract all parameters."""

import json
import logging
import re

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def parse_card_page(html: str, url: str = "") -> dict:
    """Parse an individual ad page and return structured data dict."""
    soup = BeautifulSoup(html, "lxml")
    data: dict = {}

    # Try JSON-LD structured data first
    json_ld = _extract_json_ld(soup)
    if json_ld:
        data.update(_parse_json_ld_card(json_ld))

    # Try embedded JSON state
    embedded = _extract_embedded_json(soup)
    if embedded:
        data.update(_merge_if_missing(data, _parse_embedded_state(embedded)))

    # Fallback to HTML parsing
    html_data = _parse_html_card(soup)
    data.update(_merge_if_missing(data, html_data))

    # Extract market price and deviation
    market_data = _extract_market_price(soup, html)
    data.update(_merge_if_missing(data, market_data))

    # VIN
    if not data.get("vin"):
        data["vin"] = _extract_vin(soup, html)

    # Photo URLs
    if not data.get("photo_urls"):
        data["photo_urls"] = json.dumps(_extract_photos(soup, html))

    # External ID from URL
    if not data.get("external_id") and url:
        match = re.search(r"_(\d+)$", url.split("?")[0])
        if match:
            data["external_id"] = match.group(1)

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
            data["price"] = int(float(price))

    if "image" in ld:
        images = ld["image"]
        if isinstance(images, str):
            images = [images]
        data["photo_urls"] = json.dumps(images)

    if "vehicleIdentificationNumber" in ld:
        data["vin"] = ld["vehicleIdentificationNumber"]

    return data


def _extract_embedded_json(soup: BeautifulSoup) -> dict | None:
    for script in soup.find_all("script", type="application/json"):
        try:
            content = json.loads(script.string or "")
            if isinstance(content, dict):
                return content
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def _parse_embedded_state(data: dict) -> dict:
    """Walk embedded JSON looking for car ad data."""
    result: dict = {}

    def walk(obj, depth=0):
        if depth > 15 or result.get("_found"):
            return
        if not isinstance(obj, dict):
            return

        # Look for item data
        if "item" in obj and isinstance(obj["item"], dict):
            item = obj["item"]
            result["title"] = item.get("title", result.get("title", ""))
            result["description"] = item.get("description", result.get("description", ""))
            result["external_id"] = str(item.get("id", result.get("external_id", "")))
            result["location"] = item.get("location", {}).get("name", "")

            price_val = item.get("price", item.get("priceDetailed", {}).get("value"))
            if price_val:
                result["price"] = int(float(str(price_val).replace(" ", "").replace("\xa0", "")))

        # Look for params
        if "params" in obj and isinstance(obj["params"], list):
            for param in obj["params"]:
                if isinstance(param, dict):
                    _map_param(param, result)

        for val in obj.values():
            if isinstance(val, dict):
                walk(val, depth + 1)
            elif isinstance(val, list):
                for v in val:
                    if isinstance(v, dict):
                        walk(v, depth + 1)

    walk(data)
    return result


PARAM_MAPPING = {
    "Марка": "brand",
    "Модель": "model",
    "Год выпуска": "year",
    "Пробег": "mileage_km",
    "Тип двигателя": "engine_type",
    "Объём двигателя": "engine_volume",
    "Мощность": "engine_power_hp",
    "Коробка передач": "transmission",
    "Привод": "drive_type",
    "Тип кузова": "body_type",
    "Цвет": "color",
    "Руль": "steering_wheel",
    "VIN": "vin",
}


def _map_param(param: dict, result: dict):
    """Map a parameter dict {title: ..., value: ...} to result fields."""
    title = param.get("title", param.get("label", ""))
    value = param.get("value", param.get("description", ""))

    if not title or not value:
        return

    field_name = PARAM_MAPPING.get(title)
    if not field_name:
        return

    if field_name in ("year", "engine_power_hp") or field_name == "mileage_km":
        digits = re.sub(r"[^\d]", "", str(value))
        if digits:
            result[field_name] = int(digits)
    elif field_name == "engine_volume":
        match = re.search(r"(\d+[.,]?\d*)", str(value))
        if match:
            result[field_name] = float(match.group(1).replace(",", "."))
    else:
        result[field_name] = str(value)


def _parse_html_card(soup: BeautifulSoup) -> dict:
    """Parse ad parameters from HTML elements."""
    data: dict = {}

    # Title
    title_el = soup.find("h1", attrs={"data-marker": "item-view/title-info"})
    if not title_el:
        title_el = soup.find("h1")
    if title_el:
        data["title"] = title_el.get_text(strip=True)

    # Price
    price_el = soup.find("span", attrs={"data-marker": "item-view/item-price"})
    if not price_el:
        price_el = soup.find("span", attrs={"itemprop": "price"})
    if price_el:
        price_text = price_el.get("content", price_el.get_text())
        digits = re.sub(r"[^\d]", "", str(price_text))
        if digits:
            data["price"] = int(digits)

    # Description
    desc_el = soup.find("div", attrs={"data-marker": "item-view/item-description"})
    if desc_el:
        data["description"] = desc_el.get_text(strip=True)

    # Parameters from list
    params_section = soup.find("ul", attrs={"data-marker": "item-view/item-params"})
    if params_section:
        for li in params_section.find_all("li"):
            text = li.get_text(": ", strip=True)
            for label, field_name in PARAM_MAPPING.items():
                if label.lower() in text.lower():
                    value_part = text.split(":")[-1].strip() if ":" in text else text
                    _apply_param_value(data, field_name, value_part)
                    break

    # Seller
    seller_el = soup.find("div", attrs={"data-marker": "seller-info/name"})
    if seller_el:
        data["seller_name"] = seller_el.get_text(strip=True)

    # Location
    location_el = soup.find("span", attrs={"data-marker": "item-view/item-address"})
    if not location_el:
        location_el = soup.find("div", attrs={"data-marker": "delivery/location"})
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


def _extract_market_price(soup: BeautifulSoup, html: str) -> dict:
    """Extract market/estimated price from Avito's price analysis widget."""
    data: dict = {}

    # Look for market price in data attributes or embedded JSON
    market_pattern = re.compile(r'"marketPrice"\s*:\s*(\d+)')
    match = market_pattern.search(html)
    if match:
        data["market_price"] = int(match.group(1))
        return data

    estimated_pattern = re.compile(r'"estimatedPrice"\s*:\s*(\d+)')
    match = estimated_pattern.search(html)
    if match:
        data["market_price"] = int(match.group(1))
        return data

    # Look for "Оценка стоимости" section
    for text_node in soup.find_all(string=re.compile(r"[Оо]ценка\s+стоимости|[Рр]ыночная\s+стоимость")):
        parent = text_node.parent
        if parent:
            price_text = parent.find_next(string=re.compile(r"[\d\s]+₽"))
            if price_text:
                digits = re.sub(r"[^\d]", "", str(price_text))
                if digits:
                    data["market_price"] = int(digits)
                    break

    return data


def _extract_vin(soup: BeautifulSoup, html: str) -> str | None:
    """Extract VIN number from page."""
    # JSON pattern
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
    """Extract photo URLs from ad page."""
    photos: list[str] = []

    # JSON embedded images
    img_pattern = re.compile(r'"(https://\d+\.img\.avito\.st/image/\d+/[^"]+)"')
    for match in img_pattern.finditer(html):
        url = match.group(1)
        if url not in photos:
            photos.append(url)

    if photos:
        return photos

    # Fallback: og:image and gallery images
    for meta in soup.find_all("meta", property="og:image"):
        url = meta.get("content", "")
        if url and url not in photos:
            photos.append(url)

    for img in soup.find_all("img", attrs={"data-marker": re.compile(r"image")}):
        src = img.get("src", img.get("data-src", ""))
        if src and src.startswith("http") and src not in photos:
            photos.append(src)

    return photos


def _merge_if_missing(base: dict, extra: dict) -> dict:
    """Return dict of extra items that are missing from base."""
    result = {}
    for key, value in extra.items():
        if key not in base or base[key] is None:
            result[key] = value
    return result
