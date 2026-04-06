"""Avito detail page parser — extracts full car specs from individual listing pages.

Avito embeds car specs in JSON within HTML as objects with attributeId/title/description.
Also extracts description, photos, seller info, location, listing date, and other
structured data from various JSON blocks in window.__preloadedState__.
"""

from __future__ import annotations

import json
import logging
import re

from app.parsers.base import ParsedListing

logger = logging.getLogger(__name__)

# Map Avito attribute titles → our field names
TITLE_MAP = {
    # Mileage
    "Пробег": "mileage_raw",
    # Transmission
    "Коробка передач": "transmission",
    # Drive
    "Привод": "drive_type",
    # Body
    "Кузов": "body_type",
    "Тип кузова": "body_type",
    # Color
    "Цвет кузова": "color",
    "Цвет": "color",
    # Engine
    "Двигатель": "engine_raw",
    "Тип двигателя": "engine_type",
    "Объём двигателя": "engine_volume_raw",
    "Мощность двигателя": "power_raw",
    "Мощность": "power_raw",
    # VIN
    "VIN или номер кузова": "vin",
    "VIN": "vin",
    # Owners
    "Владельцы": "owners_raw",
    "Владельцев по ПТС": "owners_raw",
    "Количество по ПТС": "owners_raw",
    "Количество владельцев": "owners_raw",
    # Steering
    "Руль": "steering",
    # Condition
    "Состояние": "condition",
    # PTS type
    "ПТС": "pts_type",
    "Тип ПТС": "pts_type",
    # Customs
    "Растаможен": "customs_raw",
    "Таможня": "customs_raw",
    # Generation / modification
    "Поколение": "generation",
    "Модификация": "modification",
    "Комплектация": "modification",
    # Damage / accident
    "Повреждения": "damage_raw",
    "ДТП": "accidents_raw",
    "Участие в ДТП": "accidents_raw",
    # Warranty
    "Гарантия": "warranty_raw",
    # Exchange
    "Обмен": "exchange_raw",
}


def enrich_listing_from_detail(listing: ParsedListing, html: str) -> ParsedListing:
    """Parse an Avito detail page HTML and enrich the listing with full specs.

    Extracts ALL available data from the detail page:
    - Car specs (engine, transmission, drive, body, color, mileage, VIN, etc.)
    - PTS type, customs status, condition, damage/accident info
    - Generation, modification/trim
    - Seller info (type, name, dealer flag)
    - Location (city, region)
    - Listing date
    - Photos (full-size URLs)
    - Avito price estimate
    - Any extra attributes go into raw_data
    """
    params = _extract_params_from_json(html)

    if listing.raw_data is None:
        listing.raw_data = {}

    # ── Car specs from attribute blocks ──────────────────────────────

    # Mileage
    if "mileage_raw" in params and listing.mileage is None:
        listing.mileage = _parse_int(params["mileage_raw"])

    # Transmission
    if "transmission" in params and not listing.transmission:
        listing.transmission = params["transmission"]

    # Drive type
    if "drive_type" in params and not listing.drive_type:
        listing.drive_type = params["drive_type"]

    # Body type
    if "body_type" in params and not listing.body_type:
        listing.body_type = params["body_type"]

    # Color
    if "color" in params and not listing.color:
        listing.color = params["color"]

    # Engine type
    if "engine_type" in params and not listing.engine_type:
        listing.engine_type = params["engine_type"]

    # Engine from compound string "2.0 л / 150 л.с. / Бензин"
    if "engine_raw" in params:
        _parse_engine(listing, params["engine_raw"])

    # Engine volume
    if "engine_volume_raw" in params and listing.engine_volume is None:
        m = re.search(r"(\d+[.,]\d+)", params["engine_volume_raw"])
        if m:
            listing.engine_volume = float(m.group(1).replace(",", "."))

    # Power
    if "power_raw" in params and listing.power_hp is None:
        listing.power_hp = _parse_int(params["power_raw"])

    # VIN
    if "vin" in params and not listing.vin:
        listing.vin = params["vin"]

    # Owners count
    if "owners_raw" in params and listing.owners_count is None:
        listing.owners_count = _parse_int(params["owners_raw"])

    # Steering wheel
    if "steering" in params and not listing.steering_wheel:
        listing.steering_wheel = params["steering"]

    # Condition
    if "condition" in params and not listing.condition:
        listing.condition = params["condition"]

    # ── New fields: PTS, customs, generation, modification ───────────

    # PTS type (original / duplicate / electronic)
    if "pts_type" in params and not listing.pts_type:
        listing.pts_type = params["pts_type"]

    # Customs cleared
    if "customs_raw" in params and listing.customs_cleared is None:
        listing.customs_cleared = _parse_bool_russian(params["customs_raw"])

    # Generation
    if "generation" in params and not listing.generation:
        listing.generation = params["generation"]

    # Modification / trim
    if "modification" in params and not listing.modification:
        listing.modification = params["modification"]

    # ── Extra attribute data → raw_data ──────────────────────────────

    if "damage_raw" in params:
        listing.raw_data["damage"] = params["damage_raw"]

    if "accidents_raw" in params:
        listing.raw_data["accidents"] = params["accidents_raw"]

    if "warranty_raw" in params:
        listing.raw_data["warranty"] = params["warranty_raw"]

    if "exchange_raw" in params:
        listing.raw_data["exchange"] = params["exchange_raw"]

    # ── Description ──────────────────────────────────────────────────

    if not listing.description:
        desc = _extract_description(html)
        if desc:
            listing.description = desc

    # ── Photos ───────────────────────────────────────────────────────

    if not listing.photos:
        photos = _extract_photos(html)
        if photos:
            listing.photos = photos
            listing.photo_count = len(photos)
    elif listing.photo_count == 0:
        listing.photo_count = len(listing.photos)

    # ── Seller info ──────────────────────────────────────────────────

    seller = _extract_seller_info(html)
    if seller:
        if not listing.seller_name and seller.get("name"):
            listing.seller_name = seller["name"]
        if not listing.seller_type and seller.get("type"):
            listing.seller_type = seller["type"]
        if not listing.is_dealer and seller.get("is_dealer"):
            listing.is_dealer = True
        # Store extra seller info
        for key in ("id", "rating", "reviews_count", "registered_date"):
            if key in seller:
                listing.raw_data[f"seller_{key}"] = seller[key]

    # ── Location (city / region) ─────────────────────────────────────

    location = _extract_location(html)
    if location:
        if not listing.city and location.get("city"):
            listing.city = location["city"]
        if not listing.region and location.get("region"):
            listing.region = location["region"]

    # ── Listing date ─────────────────────────────────────────────────

    if not listing.listing_date:
        listing_date = _extract_listing_date(html)
        if listing_date:
            listing.listing_date = listing_date

    # ── Avito price estimate ─────────────────────────────────────────

    avito_estimate = _extract_price_estimate(html)
    if avito_estimate:
        listing.raw_data["avito_estimate"] = avito_estimate

    # ── Store any unmapped attributes for future use ─────────────────

    unmapped = _extract_all_unmapped_attrs(html)
    if unmapped:
        listing.raw_data["extra_attrs"] = unmapped

    if not params and not listing.photos:
        logger.debug("No params or photos found for %s", listing.external_id)

    return listing


def _extract_params_from_json(html: str) -> dict[str, str]:
    """Extract car parameters from Avito detail page.

    Avito encodes JSON with escaped quotes (\\" instead of ") inside
    window.__preloadedState__. We need to unescape before parsing.
    The data is in objects like: {"attributeId":NNN,"description":"VALUE","title":"TITLE"}
    """
    params: dict[str, str] = {}

    # Unescape the HTML — Avito double-encodes JSON strings
    unescaped = html.replace('\\"', '"').replace("\\\\", "\\")

    # Find all JSON-like blocks with attributeId
    block_pattern = re.compile(
        r'\{"attributeId"\s*:\s*\d+[^}]*\}',
    )

    for block_match in block_pattern.finditer(unescaped):
        block = block_match.group()
        try:
            obj = json.loads(block)
            title = obj.get("title", "")
            description = obj.get("description", "")
            if title in TITLE_MAP and description:
                field = TITLE_MAP[title]
                if field not in params:
                    params[field] = description
        except (json.JSONDecodeError, KeyError):
            title_m = re.search(r'"title"\s*:\s*"([^"]*)"', block)
            desc_m = re.search(r'"description"\s*:\s*"([^"]*)"', block)
            if title_m and desc_m:
                title = title_m.group(1)
                description = desc_m.group(1)
                if title in TITLE_MAP and description:
                    field = TITLE_MAP[title]
                    if field not in params:
                        params[field] = description

    # Also extract from "descriptions":["VALUE"],"title":"TITLE" pattern
    desc_pattern = re.compile(r'"descriptions"\s*:\s*\["([^"]+)"\]\s*,\s*"title"\s*:\s*"([^"]+)"')
    for m in desc_pattern.finditer(unescaped):
        value = m.group(1)
        title = m.group(2)
        if title in TITLE_MAP and value:
            field = TITLE_MAP[title]
            if field not in params:
                params[field] = value

    if params:
        logger.debug("Extracted %d params: %s", len(params), list(params.keys()))

    return params


def _extract_all_unmapped_attrs(html: str) -> dict[str, str]:
    """Extract all attribute title/description pairs NOT in TITLE_MAP.

    Captures any Avito attributes we haven't explicitly mapped, so nothing
    is silently lost. Stored in raw_data["extra_attrs"].
    """
    extra: dict[str, str] = {}
    mapped_titles = set(TITLE_MAP.keys())

    unescaped = html.replace('\\"', '"').replace("\\\\", "\\")
    block_pattern = re.compile(r'\{"attributeId"\s*:\s*\d+[^}]*\}')

    for block_match in block_pattern.finditer(unescaped):
        block = block_match.group()
        title = None
        description = None
        try:
            obj = json.loads(block)
            title = obj.get("title", "")
            description = obj.get("description", "")
        except (json.JSONDecodeError, KeyError):
            title_m = re.search(r'"title"\s*:\s*"([^"]*)"', block)
            desc_m = re.search(r'"description"\s*:\s*"([^"]*)"', block)
            if title_m and desc_m:
                title = title_m.group(1)
                description = desc_m.group(1)

        if title and description and title not in mapped_titles and title not in extra:
            extra[title] = description

    return extra


def _extract_description(html: str) -> str | None:
    """Extract listing description from the page."""
    m = re.search(r'"description"\s*:\s*"((?:[^"\\]|\\.){20,})"', html)
    if m:
        try:
            text = json.loads(f'"{m.group(1)}"')
            if len(text) > 20:
                return text
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    return None


def _extract_photos(html: str) -> list[str]:
    """Extract full-size photo URLs from the detail page.

    Avito stores images in multiple formats. We prefer the largest available
    size (1280x960 > 640x480 > originals). The images appear in JSON as
    arrays of objects with size keys or as "images" arrays.
    """
    photos: list[str] = []
    seen: set[str] = set()

    unescaped = html.replace('\\"', '"').replace("\\\\", "\\")

    # Pattern 1: image objects with size keys like "1280x960":"URL"
    # e.g. {"1280x960":"https://...jpg","640x480":"https://...jpg"}
    img_block_pattern = re.compile(r'\{[^{}]*"(?:1280x960|640x480)"\s*:\s*"(https?://[^"]+)"[^{}]*\}')
    for m in img_block_pattern.finditer(unescaped):
        url = m.group(1)
        if url not in seen:
            seen.add(url)
            photos.append(url)

    # Pattern 2: "xxxFullImageUrl":"URL" or similar named fields
    for pattern in [
        r'"(?:\w*[Ff]ull[Ii]mage(?:Url)?|origImage|image1280x960)"\s*:\s*"(https?://[^"]+)"',
    ]:
        for m in re.finditer(pattern, unescaped):
            url = m.group(1)
            if url not in seen and _is_photo_url(url):
                seen.add(url)
                photos.append(url)

    # Pattern 3: Collect all unique image URLs from the page (avito CDN)
    # Avito images are on *.avito.st domain
    avito_img_pattern = re.compile(r'"(https?://\d+\.img\.avito\.st/image/\d+/[^"]+)"')
    for m in avito_img_pattern.finditer(unescaped):
        url = m.group(1)
        if url not in seen and _is_photo_url(url):
            seen.add(url)
            photos.append(url)

    return photos


def _is_photo_url(url: str) -> bool:
    """Check if a URL looks like a car photo (not an icon/avatar/logo)."""
    # Skip tiny thumbnails and non-image URLs
    return not any(skip in url for skip in ("avatar", "logo", "icon", "favicon", "32x32", "48x48", "64x64"))


def _extract_seller_info(html: str) -> dict[str, str | bool] | None:
    """Extract seller information from the detail page.

    Avito stores seller data in JSON with fields like:
    - sellerName / name in seller block
    - sellerType / type (private / shop / company)
    - shopId or isShop for dealer detection
    """
    info: dict[str, str | bool] = {}
    unescaped = html.replace('\\"', '"').replace("\\\\", "\\")

    # Seller name patterns
    for pattern in [
        r'"sellerName"\s*:\s*"([^"]+)"',
        r'"seller"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
        r'"shopName"\s*:\s*"([^"]+)"',
    ]:
        m = re.search(pattern, unescaped)
        if m:
            info["name"] = m.group(1)
            break

    # Seller type
    for pattern in [
        r'"sellerType"\s*:\s*"([^"]+)"',
        r'"seller"\s*:\s*\{[^}]*"type"\s*:\s*"([^"]+)"',
    ]:
        m = re.search(pattern, unescaped)
        if m:
            raw_type = m.group(1).lower()
            if raw_type in ("private", "частное лицо", "owner"):
                info["type"] = "private"
            elif raw_type in ("shop", "company", "dealer", "дилер", "компания"):
                info["type"] = "dealer"
            else:
                info["type"] = raw_type
            break

    # Dealer detection (shop presence)
    shop_match = re.search(r'"(?:shopId|isShop|isDealerPage)"\s*:\s*(\w+)', unescaped)
    if shop_match:
        val = shop_match.group(1)
        if val not in ("null", "false", "0"):
            info["is_dealer"] = True
            if "type" not in info:
                info["type"] = "dealer"

    # Also detect from category/page context
    if re.search(r'"isCompany"\s*:\s*true', unescaped):
        info["is_dealer"] = True
        if "type" not in info:
            info["type"] = "dealer"

    # Seller ID
    m = re.search(r'"sellerId"\s*:\s*(\d+)', unescaped)
    if m:
        info["id"] = m.group(1)

    # Seller rating
    m = re.search(r'"sellerRating"\s*:\s*([\d.]+)', unescaped)
    if m:
        info["rating"] = m.group(1)

    # Reviews count
    m = re.search(r'"reviewsCount"\s*:\s*(\d+)', unescaped)
    if m:
        info["reviews_count"] = m.group(1)

    # Seller registration date
    m = re.search(r'"registrationDate"\s*:\s*"([^"]+)"', unescaped)
    if m:
        info["registered_date"] = m.group(1)

    return info if info else None


def _extract_location(html: str) -> dict[str, str] | None:
    """Extract city and region from the detail page.

    Avito stores location in several JSON patterns:
    - "location":{"name":"City",...}
    - "geo":{"formattedAddress":"City, Region",...}
    - "address":"City, Region"
    """
    location: dict[str, str] = {}
    unescaped = html.replace('\\"', '"').replace("\\\\", "\\")

    # Pattern 1: location object with name
    m = re.search(r'"location"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', unescaped)
    if m:
        location["city"] = m.group(1)

    # Pattern 2: location with parentName for region
    m = re.search(r'"location"\s*:\s*\{[^}]*"parentName"\s*:\s*"([^"]+)"', unescaped)
    if m:
        location["region"] = m.group(1)

    # Pattern 3: geoReferences with district
    if "region" not in location:
        m = re.search(r'"geoReferences"\s*:\s*\[[^\]]*"content"\s*:\s*"([^"]+)"', unescaped)
        if m:
            location["region"] = m.group(1)

    # Pattern 4: address field
    if "city" not in location:
        m = re.search(r'"address"\s*:\s*"([^"]+)"', unescaped)
        if m:
            addr = m.group(1)
            parts = [p.strip() for p in addr.split(",")]
            if parts:
                location["city"] = parts[0]
                if len(parts) > 1 and "region" not in location:
                    location["region"] = parts[1]

    return location if location else None


def _extract_listing_date(html: str) -> str | None:
    """Extract the listing publication date.

    Avito stores this as:
    - "sortTimeStamp":EPOCH or "time":EPOCH
    - "publishedAt":"ISO_DATE"
    - "createdAt":"ISO_DATE"
    """
    unescaped = html.replace('\\"', '"').replace("\\\\", "\\")

    # ISO date patterns
    for pattern in [
        r'"publishedAt"\s*:\s*"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}[^"]*)"',
        r'"createdAt"\s*:\s*"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}[^"]*)"',
        r'"created"\s*:\s*"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}[^"]*)"',
    ]:
        m = re.search(pattern, unescaped)
        if m:
            return m.group(1)

    # Epoch timestamp patterns (seconds)
    for pattern in [
        r'"sortTimeStamp"\s*:\s*(\d{10,13})',
        r'"time"\s*:\s*(\d{10,13})',
        r'"createTime"\s*:\s*(\d{10,13})',
    ]:
        m = re.search(pattern, unescaped)
        if m:
            ts = int(m.group(1))
            # Convert milliseconds to seconds if needed
            if ts > 1_000_000_000_000:
                ts = ts // 1000
            # Sanity check: should be a reasonable date (2020+)
            if 1_577_836_800 < ts < 2_000_000_000:
                from datetime import UTC, datetime

                dt = datetime.fromtimestamp(ts, tz=UTC)
                return dt.isoformat()

    return None


def _extract_price_estimate(html: str) -> int | None:
    """Extract Avito's internal price estimate from the detail page.

    Avito shows "Оценка стоимости" — their market valuation.
    It appears in JSON as priceEstimate/marketPrice/valuationPrice fields.
    """
    unescaped = html.replace('\\"', '"')

    for pattern in [
        r'"marketPrice"\s*:\s*\{"value"\s*:\s*(\d+)',
        r'"priceEstimate"\s*:\s*(\d+)',
        r'"valuationPrice"\s*:\s*(\d+)',
        r'"marketValue"\s*:\s*(\d+)',
        r'"estimatedPrice"\s*:\s*(\d+)',
    ]:
        m = re.search(pattern, unescaped)
        if m:
            val = int(m.group(1))
            if 10_000 < val < 50_000_000:
                return val
    return None


def _parse_engine(listing: ParsedListing, raw: str) -> None:
    """Parse engine string like '2.0 л / 150 л.с. / Бензин' or '1.8 / Гибрид'."""
    parts = [p.strip() for p in raw.split("/")]
    for part in parts:
        # Volume: "2.0 л"
        m = re.search(r"(\d+[.,]\d+)\s*л?", part)
        if m and listing.engine_volume is None and "л.с" not in part:
            listing.engine_volume = float(m.group(1).replace(",", "."))
            continue

        # Power: "150 л.с."
        m = re.search(r"(\d+)\s*л\.?\s*с", part)
        if m and listing.power_hp is None:
            listing.power_hp = int(m.group(1))
            continue

        # Fuel type
        fuel_lower = part.lower().strip()
        if fuel_lower in ("бензин", "дизель", "гибрид", "электро", "газ") and listing.engine_type is None:
            listing.engine_type = part.strip()


def _parse_int(text: str) -> int | None:
    """Extract integer from text like '45 000 км' → 45000 or '1' → 1."""
    digits = re.sub(r"[^\d]", "", str(text))
    return int(digits) if digits else None


def _parse_bool_russian(text: str) -> bool | None:
    """Parse Russian yes/no text into boolean.

    Handles: Да/Нет, Растаможен/Не растаможен, etc.
    """
    lower = text.lower().strip()
    if lower in ("да", "растаможен", "растаможена", "оформлен", "оформлена"):
        return True
    if lower in ("нет", "не растаможен", "не растаможена", "не оформлен", "не оформлена"):
        return False
    return None
