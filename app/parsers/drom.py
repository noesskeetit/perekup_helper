"""Drom.ru parser — scrapes car listings from drom.ru search pages.

Drom.ru does NOT use Cloudflare — standard httpx works fine.
Listing pages have data in HTML (unstable CSS classes), but card URLs
can be reliably extracted via URL regex pattern.
Card pages have JSON-LD with structured data.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

import httpx
from bs4 import BeautifulSoup

from app.parsers.base import BaseParser, ParsedListing

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}

# Reliable URL pattern for Drom car listing cards (survives redesigns)
BULL_URL_RE = re.compile(r"https?://auto\.drom\.ru/\w+/\w+/\w+/(\d+)\.html")

DEFAULT_URLS = [
    # Moscow
    "https://moscow.drom.ru/toyota/?minprice=100000&maxprice=3000000",
    "https://moscow.drom.ru/hyundai/?minprice=100000&maxprice=3000000",
    "https://moscow.drom.ru/kia/?minprice=100000&maxprice=3000000",
    "https://moscow.drom.ru/bmw/?minprice=100000&maxprice=3000000",
    "https://moscow.drom.ru/nissan/?minprice=100000&maxprice=3000000",
    "https://moscow.drom.ru/volkswagen/?minprice=100000&maxprice=3000000",
    # Krasnodar
    "https://krasnodar.drom.ru/toyota/?minprice=100000&maxprice=3000000",
    "https://krasnodar.drom.ru/lada/?minprice=100000&maxprice=3000000",
    "https://krasnodar.drom.ru/hyundai/?minprice=100000&maxprice=3000000",
    # St. Petersburg
    "https://spb.drom.ru/toyota/?minprice=100000&maxprice=3000000",
    "https://spb.drom.ru/hyundai/?minprice=100000&maxprice=3000000",
    "https://spb.drom.ru/kia/?minprice=100000&maxprice=3000000",
    # Samara
    "https://samara.drom.ru/lada/?minprice=100000&maxprice=3000000",
    "https://samara.drom.ru/toyota/?minprice=100000&maxprice=3000000",
    # Ekaterinburg
    "https://ekaterinburg.drom.ru/toyota/?minprice=100000&maxprice=3000000",
    "https://ekaterinburg.drom.ru/lada/?minprice=100000&maxprice=3000000",
    "https://ekaterinburg.drom.ru/kia/?minprice=100000&maxprice=3000000",
    # Novosibirsk
    "https://novosibirsk.drom.ru/toyota/?minprice=100000&maxprice=3000000",
    "https://novosibirsk.drom.ru/hyundai/?minprice=100000&maxprice=3000000",
    # Kazan
    "https://kazan.drom.ru/toyota/?minprice=100000&maxprice=3000000",
    "https://kazan.drom.ru/lada/?minprice=100000&maxprice=3000000",
    # Rostov
    "https://rostov.drom.ru/toyota/?minprice=100000&maxprice=3000000",
    "https://rostov.drom.ru/kia/?minprice=100000&maxprice=3000000",
    # Nizhny Novgorod
    "https://nizhniynovgorod.drom.ru/toyota/?minprice=100000&maxprice=3000000",
    "https://nizhniynovgorod.drom.ru/lada/?minprice=100000&maxprice=3000000",
]


class DromParser(BaseParser):
    """Scrapes car listings from drom.ru.

    Strategy:
    1. Fetch listing pages → extract card URLs via regex (reliable across redesigns)
    2. Fetch each card page → extract data from JSON-LD (@type=Car)
    3. Fallback to __preloaded_state__ for extra fields
    """

    source_name = "drom"

    def __init__(
        self,
        urls: list[str] | None = None,
        pages_per_url: int = 3,
        max_cards_per_url: int = 30,
        pause_between_requests: float = 2.5,
    ):
        self._urls = urls or DEFAULT_URLS
        self._pages = pages_per_url
        self._max_cards = max_cards_per_url
        self._pause = pause_between_requests

    async def fetch_listings(self) -> list[ParsedListing]:
        all_listings: list[ParsedListing] = []
        seen_ids: set[str] = set()

        proxy_url = self._get_proxy_url()
        client_kwargs = {"headers": HEADERS, "timeout": 40, "follow_redirects": True}
        if proxy_url:
            client_kwargs["proxy"] = proxy_url
            logger.info("Drom: using proxy %s", proxy_url.split("@")[-1] if "@" in proxy_url else "configured")

        async with httpx.AsyncClient(**client_kwargs) as client:
            for base_url in self._urls:
                card_urls = await self._collect_card_urls(client, base_url)

                for card_url in card_urls[: self._max_cards]:
                    external_id = BULL_URL_RE.search(card_url)
                    if external_id and external_id.group(1) in seen_ids:
                        continue

                    try:
                        listing = await self._fetch_card(client, card_url)
                        if listing and listing.external_id not in seen_ids:
                            all_listings.append(listing)
                            seen_ids.add(listing.external_id)
                    except Exception:
                        logger.debug("Drom: failed to fetch card %s", card_url, exc_info=True)

                    await asyncio.sleep(self._pause)

        logger.info("DromParser fetched %d listings total", len(all_listings))
        return all_listings

    async def _collect_card_urls(self, client: httpx.AsyncClient, base_url: str) -> list[str]:
        """Collect card URLs from listing pages using reliable URL regex."""
        card_urls: list[str] = []

        for page in range(1, self._pages + 1):
            url = f"{base_url.rstrip('/')}page{page}/" if page > 1 else base_url
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.warning("Drom: %s returned %d", url, resp.status_code)
                    break

                soup = BeautifulSoup(resp.text, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if BULL_URL_RE.search(href) and href not in card_urls:
                        card_urls.append(href)

                logger.debug("Drom: %s page %d → %d card URLs so far", base_url, page, len(card_urls))
                if not card_urls:
                    break

            except httpx.TimeoutException:
                logger.warning("Drom: timeout on %s, retrying after 10s", url)
                await asyncio.sleep(10)
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        soup = BeautifulSoup(resp.text, "html.parser")
                        for a in soup.find_all("a", href=True):
                            if BULL_URL_RE.search(a["href"]) and a["href"] not in card_urls:
                                card_urls.append(a["href"])
                except Exception:
                    logger.warning("Drom: retry failed for %s", url)
                    break
            except Exception:
                logger.warning("Drom: failed to fetch listing page %s", url, exc_info=True)
                break

            await asyncio.sleep(self._pause)

        return card_urls

    async def _fetch_card(self, client: httpx.AsyncClient, url: str) -> ParsedListing | None:
        """Fetch a single card page and extract data from JSON-LD + specs table."""
        resp = await client.get(url)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        # Extract specs from HTML table (always available)
        specs = self._extract_specs_table(soup)

        # Try JSON-LD first (@type=Car)
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if data.get("@type") == "Car":
                    return self._parse_json_ld_car(data, url, specs)
            except (json.JSONDecodeError, KeyError):
                continue

        # Fallback: try __preloaded_state__
        return self._try_preloaded_state(resp.text, url)

    @staticmethod
    def _extract_specs_table(soup: BeautifulSoup) -> dict[str, str]:
        """Extract specs key-value pairs from the HTML table on card pages."""
        specs: dict[str, str] = {}
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 4:
                continue
            for row in rows:
                cells = row.find_all(["th", "td"])
                if len(cells) == 2:
                    key = cells[0].get_text(strip=True)
                    value = cells[1].get_text(strip=True)
                    if key:
                        specs[key] = value
        return specs

    def _parse_json_ld_car(self, data: dict, url: str, specs: dict[str, str] | None = None) -> ParsedListing | None:
        """Parse JSON-LD @type=Car + HTML specs table into ParsedListing."""
        if specs is None:
            specs = {}

        brand = data.get("brand", {}).get("name", "") if isinstance(data.get("brand"), dict) else str(data.get("brand", ""))
        model = str(data.get("model", ""))
        year = int(data.get("vehicleModelDate", 0) or 0)

        price = 0
        offers = data.get("offers", {})
        if isinstance(offers, dict):
            price = int(float(offers.get("price", 0)))

        mileage = None
        mileage_data = data.get("mileageFromOdometer", {})
        if isinstance(mileage_data, dict):
            mileage_str = str(mileage_data.get("value", ""))
            mileage_digits = re.sub(r"[^\d]", "", mileage_str)
            mileage = int(mileage_digits) if mileage_digits else None

        external_id = ""
        m = BULL_URL_RE.search(url)
        if m:
            external_id = m.group(1)

        image_url = ""
        img = data.get("image", {})
        if isinstance(img, dict):
            image_url = img.get("url", "")
        elif isinstance(img, str):
            image_url = img

        description = data.get("description")

        if not external_id or not brand:
            return None

        # Extract city from URL (supports both card and listing URL formats)
        from app.parsers.normalizer import extract_city_from_drom_url

        city = extract_city_from_drom_url(url)

        # VIN from JSON-LD
        vin = data.get("vehicleIdentificationNumber")

        # ── Extended fields from HTML specs table ──────────────────────────
        # Engine: "бензин, 2.0 л"
        engine_raw = specs.get("Двигатель", "")
        engine_type = None
        engine_volume = None
        if engine_raw:
            for part in engine_raw.split(","):
                fuel = part.strip().lower()
                if fuel in ("бензин", "дизель", "гибрид", "электро", "газ"):
                    engine_type = part.strip()
            vol_m = re.search(r"(\d+[.,]\d+)\s*л", engine_raw)
            if vol_m:
                engine_volume = float(vol_m.group(1).replace(",", "."))

        # Power: "148 л.с.,налог" or "148 л.с."
        power_hp = None
        power_raw = specs.get("Мощность", "")
        if power_raw:
            pow_m = re.search(r"(\d+)\s*л\.?\s*с", power_raw)
            if pow_m:
                power_hp = int(pow_m.group(1))

        # Transmission: "вариатор", "автомат", "механика"
        transmission = specs.get("Коробка передач")

        # Drive: "4WD", "передний", "задний", "полный"
        drive_type = specs.get("Привод")

        # Body type: "джип/suv 5 дв.", "седан", "хэтчбек"
        body_type = specs.get("Тип кузова")

        # Color
        color = specs.get("Цвет")

        # Steering wheel
        steering = specs.get("Руль")

        # Owners count: "4 и более", "1", "2"
        owners_count = None
        owners_raw = specs.get("Владельцы")
        if owners_raw:
            digits = re.sub(r"[^\d]", "", owners_raw)
            owners_count = int(digits) if digits else None

        # Generation
        generation = specs.get("Поколение")

        # Modification / trim
        modification = specs.get("Комплектация")

        return ParsedListing(
            source="drom",
            external_id=external_id,
            brand=brand,
            model=model,
            year=year,
            price=price,
            url=url,
            mileage=mileage,
            description=description,
            photos=[image_url] if image_url else [],
            city=city,
            vin=vin,
            engine_type=engine_type,
            engine_volume=engine_volume,
            power_hp=power_hp,
            transmission=transmission,
            drive_type=drive_type,
            body_type=body_type,
            color=color,
            steering_wheel=steering,
            owners_count=owners_count,
            generation=generation,
            modification=modification,
            raw_data={"specs": specs} if specs else None,
        )

    def _try_preloaded_state(self, html: str, url: str) -> ParsedListing | None:
        """Fallback: extract data from window.__preloaded_state__."""
        m = re.search(r"window\.__preloaded_state__\s*=\s*(\{.*?\});", html, re.DOTALL)
        if not m:
            return None

        try:
            state = json.loads(m.group(1))
        except json.JSONDecodeError:
            return None

        # Extract basic info from bullDescription
        desc = state.get("bullDescription", {})
        title = desc.get("title", "")
        price = desc.get("price", 0)

        fields = {f.get("title", ""): f.get("value", "") for f in desc.get("fields", [])}

        brand, model = self._extract_brand_model(title)
        year = self._extract_year(title)

        external_id = ""
        match = BULL_URL_RE.search(url)
        if match:
            external_id = match.group(1)

        # Photos from gallery
        photos = []
        gallery = state.get("gallery", {}).get("photos", {}).get("images", [])
        for img in gallery[:5]:
            if isinstance(img, dict):
                photos.append(img.get("src", ""))

        if not external_id or not brand:
            return None

        from app.parsers.normalizer import extract_city_from_drom_url

        # Parse structured fields from bullDescription
        transmission = fields.get("Коробка передач") or fields.get("КПП")
        drive_type = fields.get("Привод")
        body_type = fields.get("Кузов") or fields.get("Тип кузова")
        color = fields.get("Цвет")
        steering = fields.get("Руль")
        owners_raw = fields.get("Владельцы") or fields.get("Владельцев по ПТС")
        owners_count = None
        if owners_raw:
            digits = re.sub(r"[^\d]", "", owners_raw)
            owners_count = int(digits) if digits else None

        # Parse engine compound field: "2.0 л, 150 л.с., бензин"
        engine_raw = fields.get("Двигатель", "")
        engine_volume = None
        power_hp = None
        engine_type = None
        if engine_raw:
            vol_m = re.search(r"(\d+[.,]\d+)\s*л", engine_raw)
            if vol_m:
                engine_volume = float(vol_m.group(1).replace(",", "."))
            pow_m = re.search(r"(\d+)\s*л\.?\s*с", engine_raw)
            if pow_m:
                power_hp = int(pow_m.group(1))
            for part in engine_raw.split(","):
                fuel = part.strip().lower()
                if fuel in ("бензин", "дизель", "гибрид", "электро", "газ"):
                    engine_type = part.strip()

        # Mileage from fields
        mileage = None
        mileage_raw = fields.get("Пробег", "")
        if mileage_raw:
            digits = re.sub(r"[^\d]", "", mileage_raw)
            mileage = int(digits) if digits else None

        return ParsedListing(
            source="drom",
            external_id=external_id,
            brand=brand,
            model=model,
            year=year,
            price=int(price) if price else 0,
            url=url,
            mileage=mileage,
            photos=[p for p in photos if p],
            city=extract_city_from_drom_url(url),
            transmission=transmission,
            drive_type=drive_type,
            body_type=body_type,
            color=color,
            steering_wheel=steering,
            owners_count=owners_count,
            engine_type=engine_type,
            engine_volume=engine_volume,
            power_hp=power_hp,
            raw_data={"fields": fields} if fields else None,
        )

    @staticmethod
    def _get_proxy_url() -> str | None:
        """Get proxy URL from environment."""
        import os

        proxy_string = os.environ.get("PROXY_STRING", "")
        if proxy_string:
            proxy_type = os.environ.get("PROXY_TYPE", "socks5")
            return f"{proxy_type}://{proxy_string}"
        return None

    @staticmethod
    def _extract_brand_model(title: str) -> tuple[str, str]:
        clean = re.split(r",\s*\d{4}", title)[0]
        clean = re.split(r"\d+\.\d+", clean)[0].strip()
        parts = clean.split(None, 1)
        brand = parts[0] if parts else title
        model = parts[1].strip() if len(parts) > 1 else ""
        return brand, model

    @staticmethod
    def _extract_year(title: str) -> int:
        m = re.search(r"\b(19|20)\d{2}\b", title)
        return int(m.group()) if m else 0
