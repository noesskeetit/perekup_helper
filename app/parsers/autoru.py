"""Auto.ru parser — fetches car listings from Auto.ru.

Auto.ru uses Yandex SmartWebSecurity (NOT Cloudflare).
Standard httpx is blocked by TLS fingerprinting — curl_cffi with Chrome
impersonation is required. Session cookies must be warmed up.

Data is embedded in HTML as window.__INITIAL_STATE__ JSON.

Performance: randomized pauses, UA rotation, captcha recovery with IP change.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import time

from app.parsers.base import BaseParser, ParsedListing

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
]

HEADERS = {
    "User-Agent": random.choice(USER_AGENTS),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://auto.ru/",
}

# Default search URLs — geo_id appended as query param
# geo_ids: Moscow=213, SPb=2, Krasnodar=35, Samara=51
DEFAULT_SEARCHES = [
    {"url": "https://auto.ru/cars/toyota/used/?geo_id=213&price_from=100000&price_to=3000000"},
    {"url": "https://auto.ru/cars/hyundai/used/?geo_id=213&price_from=100000&price_to=3000000"},
    {"url": "https://auto.ru/cars/kia/used/?geo_id=213&price_from=100000&price_to=3000000"},
    {"url": "https://auto.ru/cars/toyota/used/?geo_id=35&price_from=100000&price_to=3000000"},
    {"url": "https://auto.ru/cars/vaz/used/?geo_id=35&price_from=100000&price_to=3000000"},
    {"url": "https://auto.ru/cars/hyundai/used/?geo_id=2&price_from=100000&price_to=3000000"},
    {"url": "https://auto.ru/cars/kia/used/?geo_id=2&price_from=100000&price_to=3000000"},
    {"url": "https://auto.ru/cars/vaz/used/?geo_id=51&price_from=100000&price_to=3000000"},
    {"url": "https://auto.ru/cars/toyota/used/?geo_id=51&price_from=100000&price_to=3000000"},
]

# Auto.ru stores data in __SSR_DATA__ (previously was __INITIAL_STATE__)
SSR_DATA_RE = re.compile(r"window\.(__SSR_DATA__|__INITIAL_STATE__)\s*=\s*(\{.+?\});\s*(?:</script>|$)", re.DOTALL)


class AutoruParser(BaseParser):
    """Fetches car listings from Auto.ru using curl_cffi (Chrome impersonation).

    Uses window.__INITIAL_STATE__ from server-rendered HTML pages.
    Falls back gracefully if curl_cffi is not installed or captcha is triggered.
    """

    source_name = "autoru"

    def __init__(
        self,
        searches: list[dict] | None = None,
        pages_per_search: int = 2,
        pause_range: tuple[float, float] = (2.0, 3.5),
    ):
        self._searches = searches or DEFAULT_SEARCHES
        self._pages = pages_per_search
        self._pause_range = pause_range

    async def fetch_listings(self) -> list[ParsedListing]:
        all_listings: list[ParsedListing] = []
        seen_ids: set[str] = set()

        try:
            from curl_cffi import requests as cffi_requests
        except ImportError:
            logger.warning("Auto.ru parser requires curl_cffi. Install: pip install curl_cffi")
            return []

        # Run synchronous curl_cffi in a thread
        return await asyncio.to_thread(self._run_sync, cffi_requests)

    def _run_sync(self, cffi_requests) -> list[ParsedListing]:
        all_listings: list[ParsedListing] = []
        seen_ids: set[str] = set()

        session = cffi_requests.Session(impersonate="chrome")
        # Rotate UA at session level
        headers = {**HEADERS, "User-Agent": random.choice(USER_AGENTS)}
        session.headers.update(headers)

        # Set proxy if available
        proxy_string = os.environ.get("PROXY_STRING", "")
        if proxy_string:
            proxy_type = os.environ.get("PROXY_TYPE", "socks5")
            proxy_url = f"{proxy_type}://{proxy_string}"
            session.proxies = {"http": proxy_url, "https": proxy_url}
            logger.info("Auto.ru: using proxy")

        # Load pre-warmed cookies from Playwright
        cookies_path = os.path.join("storage", "autoru_cookies.json")
        if os.path.exists(cookies_path):
            import json

            with open(cookies_path, encoding="utf-8") as f:
                saved_cookies = json.load(f)
            session.cookies.update(saved_cookies)
            logger.info("Auto.ru: loaded %d pre-warmed cookies", len(saved_cookies))
        else:
            logger.warning("Auto.ru: no pre-warmed cookies, trying cold start")
            try:
                session.get("https://auto.ru/", timeout=15)
                time.sleep(2)
            except Exception:
                logger.debug("Auto.ru: failed to warm up session")

        captcha_count = 0
        for search in self._searches:
            base_url = search["url"]

            for page in range(1, self._pages + 1):
                url = f"{base_url}&page={page}" if "page=" not in base_url else base_url

                try:
                    # Rotate UA per request
                    session.headers["User-Agent"] = random.choice(USER_AGENTS)
                    resp = session.get(url, timeout=60)

                    # Check for captcha — change IP and skip to next search URL
                    if "captcha.auto.ru" in str(resp.url) or resp.status_code == 403:
                        captcha_count += 1
                        logger.warning("Auto.ru: captcha #%d triggered on %s, changing IP and skipping to next search",
                                       captcha_count, base_url)
                        self._try_change_ip()
                        time.sleep(5)
                        break  # Skip to next search URL, not stopping entirely

                    if resp.status_code == 429:
                        logger.warning("Auto.ru: rate limited, changing IP and backing off")
                        self._try_change_ip()
                        time.sleep(10)
                        break

                    if resp.status_code != 200:
                        logger.warning("Auto.ru: %s returned %d", url, resp.status_code)
                        break

                    page_listings = self._extract_offers(resp.text)
                    for listing in page_listings:
                        if listing.external_id not in seen_ids:
                            all_listings.append(listing)
                            seen_ids.add(listing.external_id)

                    logger.debug("Auto.ru: %s page %d → %d offers", base_url, page, len(page_listings))

                    if not page_listings:
                        break

                except Exception:
                    logger.warning("Auto.ru: failed to fetch %s", url, exc_info=True)
                    break

                time.sleep(random.uniform(*self._pause_range))

        logger.info("AutoruParser fetched %d listings total (captchas hit: %d)", len(all_listings), captcha_count)
        return all_listings

    @staticmethod
    def _try_change_ip() -> None:
        """Attempt to rotate proxy IP via the proxy manager."""
        try:
            from app.parsers.proxy_manager import change_ip
            change_ip()
        except Exception:
            logger.debug("Auto.ru: IP change failed", exc_info=True)

    def _extract_offers(self, html: str) -> list[ParsedListing]:
        """Extract offers from Auto.ru HTML via regex."""
        logger.info("Auto.ru: extracting from %d bytes, has mark_info=%s, listing_urls=%d",
                     len(html), "mark_info" in html,
                     html.count("auto.ru/cars/used/sale/"))
        return self._extract_offers_regex(html)

    def _extract_offers_regex(self, html: str) -> list[ParsedListing]:
        """Extract offers via regex — match IDs between URLs and data blocks.

        Auto.ru React SSR places URLs and data in separate parts of HTML.
        We extract unique offer IDs from URLs, then find their data (price, mark, year)
        by searching for the numeric part of the ID near data fields.
        """
        # Step 1: Collect unique offer URLs and IDs
        url_pattern = re.compile(r"auto\.ru/cars/used/sale/(\w+)/(\w+)/(\d+)-([a-f0-9]+)/")
        offer_urls: dict[str, dict] = {}

        for m in url_pattern.finditer(html):
            numeric_id = m.group(3)  # e.g. "1132037070"
            full_id = f"{numeric_id}-{m.group(4)}"
            if numeric_id not in offer_urls:
                offer_urls[numeric_id] = {
                    "full_id": full_id,
                    "brand_slug": m.group(1),
                    "model_slug": m.group(2),
                    "url": f"https://auto.ru/cars/used/sale/{m.group(1)}/{m.group(2)}/{full_id}/",
                }

        if not offer_urls:
            return []

        # Step 2: Find data blocks by numeric ID proximity
        # Data is near patterns like "id":"1132037070" in the data section
        listings = []
        for numeric_id, url_info in offer_urls.items():
            # Search for this ID in the data portion of HTML
            id_pattern = re.compile(rf'"{numeric_id}"')
            for id_match in id_pattern.finditer(html):
                # Search wide context around this ID occurrence
                start = max(0, id_match.start() - 5000)
                end = min(len(html), id_match.end() + 5000)
                chunk = html[start:end]

                price_m = re.search(r'"price":(\d{5,})', chunk)
                if not price_m:
                    continue

                year_m = re.search(r'"year":(\d{4})', chunk)
                mileage_m = re.search(r'"mileage":(\d+)', chunk)
                mark_m = re.search(r'"mark_info":\{[^}]*?"name":"([^"]+)"', chunk)
                model_m = re.search(r'"model_info":\{[^}]*?"name":"([^"]+)"', chunk)

                # Extract additional fields from context
                engine_type_m = re.search(r'"engine_type":"([^"]+)"', chunk)
                transmission_m = re.search(r'"transmission":"([^"]+)"', chunk)
                drive_m = re.search(r'"drive":"([^"]+)"', chunk)
                body_m = re.search(r'"body_type"(?:_group)?":"([^"]+)"', chunk)
                color_m = re.search(r'"color":\{[^}]*?"name":"([^"]+)"', chunk)
                vin_m = re.search(r'"vin":"([^"]+)"', chunk)
                seller_m = re.search(r'"seller":\{[^}]*?"name":"([^"]+)"', chunk)
                city_m = re.search(r'"city":"([^"]+)"', chunk)
                region_m = re.search(r'"region_info":\{[^}]*?"name":"([^"]+)"', chunk)
                # Auto.ru has no standalone "city" field; location is in region_info.name
                # Fall back to region_info name as city when city regex misses
                if not city_m and region_m:
                    city_m = region_m
                gen_m = re.search(r'"super_gen":\{[^}]*?"name":"([^"]+)"', chunk)
                engine_vol_m = re.search(r'"engine_volume":(\d+)', chunk)
                power_m = re.search(r'"engine_power":(\d+)', chunk)

                brand = mark_m.group(1) if mark_m else url_info["brand_slug"].replace("_", " ").title()
                model_name = model_m.group(1) if model_m else url_info["model_slug"].replace("_", " ").title()

                # Engine volume from Auto.ru is in cc, convert to liters
                engine_vol = None
                if engine_vol_m:
                    cc = int(engine_vol_m.group(1))
                    engine_vol = round(cc / 1000, 1) if cc > 100 else float(cc)

                listings.append(
                    ParsedListing(
                        source="autoru",
                        external_id=url_info["full_id"],
                        brand=brand,
                        model=model_name,
                        year=int(year_m.group(1)) if year_m else 0,
                        price=int(price_m.group(1)),
                        url=url_info["url"],
                        mileage=int(mileage_m.group(1)) if mileage_m else None,
                        engine_type=engine_type_m.group(1) if engine_type_m else None,
                        engine_volume=engine_vol,
                        power_hp=int(power_m.group(1)) if power_m else None,
                        transmission=transmission_m.group(1) if transmission_m else None,
                        drive_type=drive_m.group(1) if drive_m else None,
                        body_type=body_m.group(1) if body_m else None,
                        color=color_m.group(1) if color_m else None,
                        vin=vin_m.group(1) if vin_m else None,
                        seller_name=seller_m.group(1) if seller_m else None,
                        city=city_m.group(1) if city_m else None,
                        region=region_m.group(1) if region_m else None,
                        generation=gen_m.group(1) if gen_m else None,
                        photos=[],
                    )
                )
                break  # One data block per ID

        logger.info("Auto.ru regex extracted %d listings from %d URLs", len(listings), len(offer_urls))
        return listings

    def _parse_offer(self, offer: dict) -> ParsedListing | None:
        """Convert a single Auto.ru offer from __INITIAL_STATE__ to ParsedListing."""
        car_info = offer.get("vehicle_info", offer.get("car_info", {}))
        mark_info = car_info.get("mark_info", {})
        model_info = car_info.get("model_info", {})

        brand = mark_info.get("name", "")
        model = model_info.get("name", "")

        docs = offer.get("documents", {})
        year = docs.get("year", 0)
        if not year:
            year = car_info.get("super_gen", {}).get("year_from", 0)

        price_info = offer.get("price_info", {})
        price = int(price_info.get("RUR", price_info.get("price", price_info.get("price_rur", 0))))

        if not price or not brand:
            return None

        offer_id = str(offer.get("id", ""))
        url = offer.get("url", "")
        if not url and offer_id:
            url = f"https://auto.ru/cars/used/sale/{brand.lower()}/{model.lower()}/{offer_id}/"

        state = offer.get("state", {})
        mileage = state.get("mileage")

        # Photos
        photos = []
        for photo in (state.get("image_urls") or offer.get("photos", []))[:5]:
            if isinstance(photo, dict):
                sizes = photo.get("sizes", {})
                photo_url = sizes.get("1200x900") or sizes.get("456x342") or sizes.get("320x240", "")
                if photo_url:
                    photos.append(photo_url)
            elif isinstance(photo, str):
                photos.append(photo)

        description = offer.get("description", "")
        vin = docs.get("vin")

        raw_data = {}
        if vin:
            raw_data["vin"] = vin
        if car_info.get("engine_type"):
            raw_data["engine"] = car_info["engine_type"]
        if car_info.get("transmission"):
            raw_data["transmission"] = car_info["transmission"]

        return ParsedListing(
            source="autoru",
            external_id=offer_id,
            brand=brand,
            model=model,
            year=year,
            price=price,
            url=url,
            mileage=mileage,
            description=description,
            photos=[p for p in photos if p],
            raw_data=raw_data or None,
        )
