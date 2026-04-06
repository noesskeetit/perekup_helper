"""Avito detail page parser — extracts full car specs from individual listing pages.

Avito embeds car specs in JSON within HTML as objects with attributeId/title/description.
Also extracts description, photos, and structured data from various JSON blocks.
"""

from __future__ import annotations

import json
import logging
import re

from app.parsers.base import ParsedListing

logger = logging.getLogger(__name__)

# Map Avito attribute titles → our field names
TITLE_MAP = {
    "Пробег": "mileage_raw",
    "Коробка передач": "transmission",
    "Привод": "drive_type",
    "Кузов": "body_type",
    "Тип кузова": "body_type",
    "Цвет кузова": "color",
    "Цвет": "color",
    "Двигатель": "engine_raw",
    "Тип двигателя": "engine_type",
    "Объём двигателя": "engine_volume_raw",
    "Мощность двигателя": "power_raw",
    "Мощность": "power_raw",
    "VIN или номер кузова": "vin",
    "VIN": "vin",
    "Владельцы": "owners_raw",
    "Владельцев по ПТС": "owners_raw",
    "Количество по ПТС": "owners_raw",
    "Количество владельцев": "owners_raw",
    "Руль": "steering",
    "Состояние": "condition",
}


def enrich_listing_from_detail(listing: ParsedListing, html: str) -> ParsedListing:
    """Parse an Avito detail page HTML and enrich the listing with full specs."""
    params = _extract_params_from_json(html)

    if not params:
        logger.debug("No params found for %s", listing.external_id)
        return listing

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

    # Steering wheel (was extracted but dropped before!)
    if "steering" in params and not listing.steering_wheel:
        listing.steering_wheel = params["steering"]

    # Condition (was extracted but dropped before!)
    if "condition" in params and not listing.condition:
        listing.condition = params["condition"]

    # Description
    if not listing.description:
        desc = _extract_description(html)
        if desc:
            listing.description = desc

    # Avito price estimate (their internal market valuation)
    avito_estimate = _extract_price_estimate(html)
    if avito_estimate:
        if listing.raw_data is None:
            listing.raw_data = {}
        listing.raw_data["avito_estimate"] = avito_estimate

    return listing


def _extract_params_from_json(html: str) -> dict[str, str]:
    """Extract car parameters from Avito detail page.

    Avito encodes JSON with escaped quotes (\\" instead of ") inside
    window.__preloadedState__. We need to unescape before parsing.
    The data is in objects like: {"attributeId":NNN,"description":"VALUE","title":"TITLE"}
    """
    params: dict[str, str] = {}

    # Unescape the HTML — Avito double-encodes JSON strings
    # Replace \\" with " and \\/ with /
    unescaped = html.replace('\\"', '"').replace("\\\\", "\\")

    # Now search for attributeId patterns in the unescaped text
    # Pattern: "attributeId":NNN + "description":"VALUE" + "title":"TITLE"
    # The order varies, so we extract all attributeId blocks and parse them

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
            # Try regex extraction from the block
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


def _extract_description(html: str) -> str | None:
    """Extract listing description from the page."""
    # Try to find description in JSON
    m = re.search(r'"description"\s*:\s*"((?:[^"\\]|\\.){20,})"', html)
    if m:
        try:
            text = json.loads(f'"{m.group(1)}"')
            if len(text) > 20:
                return text
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    return None


def _extract_price_estimate(html: str) -> int | None:
    """Extract Avito's internal price estimate from the detail page.

    Avito shows "Оценка стоимости" — their market valuation.
    It appears in JSON as priceEstimate/marketPrice/valuationPrice fields.
    """
    unescaped = html.replace('\\"', '"')

    # Pattern 1: "marketPrice":{"value":NNN}
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
