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
    # Moscow (213) — price tiers + more brands
    {"url": "https://auto.ru/cars/toyota/used/?geo_id=213&price_from=100000&price_to=800000"},
    {"url": "https://auto.ru/cars/toyota/used/?geo_id=213&price_from=800000&price_to=2000000"},
    {"url": "https://auto.ru/cars/hyundai/used/?geo_id=213&price_from=100000&price_to=1500000"},
    {"url": "https://auto.ru/cars/kia/used/?geo_id=213&price_from=100000&price_to=1500000"},
    {"url": "https://auto.ru/cars/nissan/used/?geo_id=213&price_from=100000&price_to=1500000"},
    {"url": "https://auto.ru/cars/mazda/used/?geo_id=213&price_from=100000&price_to=1500000"},
    {"url": "https://auto.ru/cars/volkswagen/used/?geo_id=213&price_from=100000&price_to=1500000"},
    # Moscow — premium/tuning
    {"url": "https://auto.ru/cars/bmw/used/?geo_id=213&price_from=200000&price_to=2000000"},
    {"url": "https://auto.ru/cars/mercedes/used/?geo_id=213&price_from=200000&price_to=2000000"},
    {"url": "https://auto.ru/cars/audi/used/?geo_id=213&price_from=200000&price_to=2000000"},
    {"url": "https://auto.ru/cars/lexus/used/?geo_id=213&price_from=300000&price_to=3000000"},
    {"url": "https://auto.ru/cars/subaru/used/?geo_id=213&price_from=100000&price_to=1500000"},
    {"url": "https://auto.ru/cars/honda/used/?geo_id=213&price_from=100000&price_to=1500000"},
    # Krasnodar (35)
    {"url": "https://auto.ru/cars/toyota/used/?geo_id=35&price_from=100000&price_to=2000000"},
    {"url": "https://auto.ru/cars/vaz/used/?geo_id=35&price_from=100000&price_to=1000000"},
    {"url": "https://auto.ru/cars/hyundai/used/?geo_id=35&price_from=100000&price_to=1500000"},
    # SPb (2)
    {"url": "https://auto.ru/cars/toyota/used/?geo_id=2&price_from=100000&price_to=2000000"},
    {"url": "https://auto.ru/cars/hyundai/used/?geo_id=2&price_from=100000&price_to=1500000"},
    {"url": "https://auto.ru/cars/kia/used/?geo_id=2&price_from=100000&price_to=1500000"},
    # Samara (51)
    {"url": "https://auto.ru/cars/vaz/used/?geo_id=51&price_from=100000&price_to=1000000"},
    {"url": "https://auto.ru/cars/toyota/used/?geo_id=51&price_from=100000&price_to=1500000"},
]

# Auto.ru stores data in __SSR_DATA__ (previously was __INITIAL_STATE__)
SSR_DATA_RE = re.compile(r"window\.(__SSR_DATA__|__INITIAL_STATE__)\s*=\s*(\{.+?\});\s*(?:</script>|$)", re.DOTALL)

# ── Auto.ru color hex → human-readable name mapping ────────────────────────
# Auto.ru uses "color_hex" instead of a color name in its SSR data.
# These are the standard hex codes Auto.ru assigns to each color category.
_COLOR_HEX_MAP: dict[str, str] = {
    "040001": "black",
    "fafbfb": "white",
    "c49648": "gold",
    "cacecb": "silver",
    "97948f": "grey",
    "ee1d19": "red",
    "0000cc": "blue",
    "22a0f8": "light_blue",
    "007f00": "green",
    "200204": "brown",
    "660099": "purple",
    "dea522": "orange",
    "ffd600": "yellow",
    "ff69b4": "pink",
    "4a2197": "violet",
}


def _hex_to_color_name(hex_code: str) -> str:
    """Convert Auto.ru color_hex to a human-readable color name."""
    return _COLOR_HEX_MAP.get(hex_code.lower().lstrip("#"), hex_code)


def _parse_autoru_date(raw: str) -> str | None:
    """Convert Auto.ru date (millis string or ISO) to ISO date string."""
    if not raw:
        return None
    # Millisecond timestamp as string: "1712345678000"
    if raw.isdigit() and len(raw) >= 10:
        from datetime import UTC, datetime

        ts = int(raw)
        # If > 1e12 it's milliseconds, otherwise seconds
        if ts > 1_000_000_000_000:
            ts = ts // 1000
        try:
            dt = datetime.fromtimestamp(ts, tz=UTC)
            return dt.strftime("%Y-%m-%d")
        except (ValueError, OSError):
            return None
    # Already an ISO-ish string
    if "T" in raw or "-" in raw:
        return raw[:10]  # "2024-04-06T..." → "2024-04-06"
    return None


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

        self._load_cookies(session, cffi_requests)

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
                        logger.warning(
                            "Auto.ru: captcha #%d triggered on %s, changing IP and skipping to next search",
                            captcha_count,
                            base_url,
                        )
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

                    # Detect stale cookies: real pages are 500KB+, stubs are <50KB
                    if len(resp.text) < 50_000 and "mark_info" not in resp.text:
                        logger.warning("Auto.ru: page only %d bytes (stale cookies?), re-warming", len(resp.text))
                        if self._warmup_cookies(session):
                            # Retry this page with fresh cookies
                            resp = session.get(url, timeout=60)
                            if len(resp.text) < 50_000:
                                logger.warning("Auto.ru: still blocked after warmup, skipping search")
                                self._try_change_ip()
                                time.sleep(5)
                                break
                        else:
                            self._try_change_ip()
                            time.sleep(5)
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

        self._last_captcha_count = captcha_count
        logger.info("AutoruParser fetched %d listings total (captchas hit: %d)", len(all_listings), captcha_count)
        return all_listings

    @staticmethod
    def _load_cookies(session, cffi_requests) -> None:
        """Load pre-warmed cookies, or warmup if missing/stale."""
        cookies_path = os.path.join("storage", "autoru_cookies.json")
        if os.path.exists(cookies_path):
            with open(cookies_path, encoding="utf-8") as f:
                saved_cookies = json.load(f)
            session.cookies.update(saved_cookies)
            logger.info("Auto.ru: loaded %d pre-warmed cookies", len(saved_cookies))
        else:
            logger.warning("Auto.ru: no cookies, running inline warmup")
            AutoruParser._warmup_cookies(session)

    @staticmethod
    def _warmup_cookies(session) -> bool:
        """Inline cookie warmup: visit auto.ru main page + search page."""
        try:
            session.get("https://auto.ru/", timeout=20)
            time.sleep(2)
            resp = session.get(
                "https://auto.ru/cars/toyota/used/?geo_id=213&price_from=100000&price_to=3000000",
                timeout=120,
            )
            if len(resp.text) > 50_000 and "mark_info" in resp.text:
                # Save refreshed cookies
                cookies_dict = {c.name: c.value for c in session.cookies.jar}
                cookies_path = os.path.join("storage", "autoru_cookies.json")
                os.makedirs(os.path.dirname(cookies_path), exist_ok=True)
                with open(cookies_path, "w", encoding="utf-8") as f:
                    json.dump(cookies_dict, f, indent=2)
                logger.info("Auto.ru: inline warmup OK, saved %d cookies", len(cookies_dict))
                return True
            logger.warning("Auto.ru: inline warmup got %d bytes (no data)", len(resp.text))
        except Exception:
            logger.warning("Auto.ru: inline warmup failed", exc_info=True)
        return False

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
        logger.info(
            "Auto.ru: extracting from %d bytes, has mark_info=%s, listing_urls=%d",
            len(html),
            "mark_info" in html,
            html.count("auto.ru/cars/used/sale/"),
        )
        return self._extract_offers_regex(html)

    # ── Compiled regex patterns for field extraction ──────────────────────────
    # Auto.ru SSR data nests tech specs under "tech_param" inside "vehicle_info".
    # Field names follow the protobuf schema: displacement (cc), power (hp),
    # gear_type (drive), etc.  We also try legacy/alternative key names so the
    # parser stays resilient if Auto.ru changes the serialisation format.

    # Match price from price_info block (real price), NOT credit/monthly payments.
    # Auto.ru SSR has: "price_info":{"RUR":1534000,...} for real price
    # and "price":85487 for monthly payments in credit blocks.
    _RE_PRICE_INFO = re.compile(r'"price_info":\{[^}]*?"RUR":(\d{5,})')
    _RE_PRICE = re.compile(r'"price":(\d{5,})')
    _RE_YEAR = re.compile(r'"year":(\d{4})')
    _RE_MILEAGE = re.compile(r'"mileage":(\d+)')
    _RE_MARK = re.compile(r'"mark_info":\{[^}]*?"name":"([^"]+)"')
    _RE_MODEL = re.compile(r'"model_info":\{[^}]*?"name":"([^"]+)"')
    _RE_GEN = re.compile(r'"super_gen":\{[^}]*?"name":"([^"]+)"')

    # Engine type: "engine_type":"GASOLINE" (inside tech_param or vehicle_info)
    _RE_ENGINE_TYPE = re.compile(r'"engine_type":"([^"]+)"')
    # Transmission: "transmission":"AUTOMATIC"
    _RE_TRANSMISSION = re.compile(r'"transmission":"([^"]+)"')

    # Displacement: "displacement":2494 (cc) — primary Auto.ru key
    # Fallback: "engine_volume":2494
    _RE_DISPLACEMENT = re.compile(r'"displacement":(\d+)')
    _RE_ENGINE_VOLUME = re.compile(r'"engine_volume":(\d+)')

    # Power: "power":181 or "horse_power":181 or "power_hp":181
    _RE_POWER = re.compile(r'"(?:power|horse_power|power_hp|engine_power)":(\d+)')

    # Drive/gear type: "gear_type":"FORWARD_CONTROL" or "drive":"ALL_WHEEL_DRIVE"
    _RE_GEAR_TYPE = re.compile(r'"gear_type":"([^"]+)"')
    _RE_DRIVE = re.compile(r'"drive":"([^"]+)"')

    # Body type: inside "configuration":{"body_type":"SEDAN"} or "body_type_group":"SEDAN"
    _RE_BODY_TYPE = re.compile(r'"body_type(?:_group)?":"([^"]+)"')

    # Color: "color_hex":"040001" or "color":{"name":"..."}
    _RE_COLOR_HEX = re.compile(r'"color_hex":"([0-9a-fA-F]+)"')
    _RE_COLOR_NAME = re.compile(r'"color":\{[^}]*?"name":"([^"]+)"')

    # VIN
    _RE_VIN = re.compile(r'"vin":"([A-HJ-NPR-Z0-9]{17})"')

    # Steering wheel: "steering_wheel":"LEFT" or "LEFT_HAND_DRIVE"
    _RE_STEERING = re.compile(r'"steering_wheel":"([^"]+)"')

    # Owners: "owners_number":2 or "owners_count":2
    _RE_OWNERS = re.compile(r'"owners_(?:number|count)":(\d+)')

    # PTS: "pts":"ORIGINAL" or "pts_type":"DUPLICATE"
    _RE_PTS = re.compile(r'"pts(?:_type)?":"([^"]+)"')

    # Customs: "custom_cleared":true
    _RE_CUSTOMS = re.compile(r'"custom_cleared":(true|false)')

    # Description / seller comment
    _RE_DESCRIPTION = re.compile(r'"(?:description|seller_comment)":"((?:[^"\\]|\\.){1,2000})"')

    # Seller type: "seller_type":"PRIVATE" or "COMMERCIAL"
    _RE_SELLER_TYPE = re.compile(r'"seller_type":"([^"]+)"')
    _RE_SELLER_NAME = re.compile(r'"seller":\{[^}]*?"name":"([^"]+)"')

    # Creation date: "creation_date":"1712345678000" (millis) or ISO string
    _RE_CREATION_DATE = re.compile(r'"creation_date":"([^"]+)"')
    _RE_CREATED_AT = re.compile(r'"created(?:_at)?":"([^"]+)"')

    # Location
    _RE_CITY = re.compile(r'"city":"([^"]+)"')
    _RE_REGION = re.compile(r'"region_info":\{[^}]*?"name":"([^"]+)"')
    _RE_GEO_NAME = re.compile(r'"geobase_id":\d+[^}]*?"name":"([^"]+)"')

    # Photos: first few image URLs
    _RE_PHOTO = re.compile(r'"(?:1200x900|full|orig(?:inal)?)":\s*"(https?://[^"]+)"')

    def _extract_offers_regex(self, html: str) -> list[ParsedListing]:
        """Extract offers via regex — match IDs between URLs and data blocks.

        Auto.ru React SSR places URLs and data in separate parts of HTML.
        We extract unique offer IDs from URLs, then find their data (price, mark, year)
        by searching for the numeric part of the ID near data fields.

        The search radius is 15 000 chars because Auto.ru offer objects are large
        (~8-12 KB each) — tech_param, photos, tags, badges etc. can push fields
        far from the offer ID anchor.
        """
        SEARCH_RADIUS = 15_000

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
            id_pattern = re.compile(rf'"{numeric_id}"')
            for id_match in id_pattern.finditer(html):
                start = max(0, id_match.start() - SEARCH_RADIUS)
                end = min(len(html), id_match.end() + SEARCH_RADIUS)
                chunk = html[start:end]

                # Must have price to be a valid data block.
                # Prefer price_info.RUR (real price) over bare "price" (may be credit payment).
                price_m = self._RE_PRICE_INFO.search(chunk)
                if not price_m:
                    price_m = self._RE_PRICE.search(chunk)
                if not price_m:
                    continue

                year_m = self._RE_YEAR.search(chunk)
                mileage_m = self._RE_MILEAGE.search(chunk)
                mark_m = self._RE_MARK.search(chunk)
                model_m = self._RE_MODEL.search(chunk)

                brand = mark_m.group(1) if mark_m else url_info["brand_slug"].replace("_", " ").title()
                model_name = model_m.group(1) if model_m else url_info["model_slug"].replace("_", " ").title()

                # ── Engine type & transmission ──────────────────────────────
                engine_type_m = self._RE_ENGINE_TYPE.search(chunk)
                transmission_m = self._RE_TRANSMISSION.search(chunk)

                # ── Displacement → engine_volume (liters) ───────────────────
                engine_vol = None
                disp_m = self._RE_DISPLACEMENT.search(chunk)
                if not disp_m:
                    disp_m = self._RE_ENGINE_VOLUME.search(chunk)
                if disp_m:
                    cc = int(disp_m.group(1))
                    # Auto.ru stores displacement in cc (e.g. 2494).
                    # Values > 100 are cc; values <= 100 are already liters.
                    engine_vol = round(cc / 1000, 1) if cc > 100 else float(cc)

                # ── Power (hp) ──────────────────────────────────────────────
                power_m = self._RE_POWER.search(chunk)

                # ── Drive type ──────────────────────────────────────────────
                drive_m = self._RE_GEAR_TYPE.search(chunk)
                if not drive_m:
                    drive_m = self._RE_DRIVE.search(chunk)

                # ── Body type ───────────────────────────────────────────────
                body_m = self._RE_BODY_TYPE.search(chunk)

                # ── Color ───────────────────────────────────────────────────
                color = None
                color_hex_m = self._RE_COLOR_HEX.search(chunk)
                color_name_m = self._RE_COLOR_NAME.search(chunk)
                if color_name_m:
                    color = color_name_m.group(1)
                elif color_hex_m:
                    color = _hex_to_color_name(color_hex_m.group(1))

                # ── VIN ─────────────────────────────────────────────────────
                vin_m = self._RE_VIN.search(chunk)

                # ── Steering wheel ──────────────────────────────────────────
                steering_m = self._RE_STEERING.search(chunk)
                steering = None
                if steering_m:
                    raw = steering_m.group(1).upper()
                    if "LEFT" in raw:
                        steering = "LEFT"
                    elif "RIGHT" in raw:
                        steering = "RIGHT"
                    else:
                        steering = steering_m.group(1)

                # ── Owners count ────────────────────────────────────────────
                owners_m = self._RE_OWNERS.search(chunk)

                # ── PTS type ────────────────────────────────────────────────
                pts_m = self._RE_PTS.search(chunk)

                # ── Customs ─────────────────────────────────────────────────
                customs_m = self._RE_CUSTOMS.search(chunk)

                # ── Description ─────────────────────────────────────────────
                desc_m = self._RE_DESCRIPTION.search(chunk)
                description = None
                if desc_m:
                    # Unescape JSON string
                    raw_desc = desc_m.group(1)
                    try:
                        description = json.loads(f'"{raw_desc}"')
                    except (json.JSONDecodeError, ValueError):
                        description = raw_desc.replace("\\n", "\n").replace('\\"', '"')

                # ── Seller ──────────────────────────────────────────────────
                seller_type_m = self._RE_SELLER_TYPE.search(chunk)
                seller_name_m = self._RE_SELLER_NAME.search(chunk)

                # ── Creation/listing date ───────────────────────────────────
                listing_date = None
                date_m = self._RE_CREATION_DATE.search(chunk)
                if not date_m:
                    date_m = self._RE_CREATED_AT.search(chunk)
                if date_m:
                    listing_date = _parse_autoru_date(date_m.group(1))

                # ── Location ────────────────────────────────────────────────
                city_m = self._RE_CITY.search(chunk)
                region_m = self._RE_REGION.search(chunk)
                if not city_m:
                    city_m = self._RE_GEO_NAME.search(chunk)
                if not city_m and region_m:
                    city_m = region_m

                # ── Generation ──────────────────────────────────────────────
                gen_m = self._RE_GEN.search(chunk)

                # ── Photos ──────────────────────────────────────────────────
                photos = []
                for photo_m in self._RE_PHOTO.finditer(chunk):
                    photo_url = photo_m.group(1)
                    if photo_url not in photos:
                        photos.append(photo_url)
                    if len(photos) >= 5:
                        break

                # ── Determine seller flags ──────────────────────────────────
                seller_type_val = seller_type_m.group(1) if seller_type_m else None
                is_dealer = False
                if seller_type_val:
                    st_upper = seller_type_val.upper()
                    is_dealer = st_upper in ("COMMERCIAL", "DEALER")

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
                        color=color,
                        vin=vin_m.group(1) if vin_m else None,
                        steering_wheel=steering,
                        owners_count=int(owners_m.group(1)) if owners_m else None,
                        description=description,
                        seller_type=seller_type_val,
                        seller_name=seller_name_m.group(1) if seller_name_m else None,
                        is_dealer=is_dealer,
                        listing_date=listing_date,
                        pts_type=pts_m.group(1) if pts_m else None,
                        customs_cleared=customs_m.group(1) == "true" if customs_m else None,
                        city=city_m.group(1) if city_m else None,
                        region=region_m.group(1) if region_m else None,
                        generation=gen_m.group(1) if gen_m else None,
                        photos=photos,
                        photo_count=len(photos),
                    )
                )
                break  # One data block per ID

        logger.info("Auto.ru regex extracted %d listings from %d URLs", len(listings), len(offer_urls))
        return listings

    def _parse_offer(self, offer: dict) -> ParsedListing | None:
        """Convert a single Auto.ru offer from __INITIAL_STATE__ JSON to ParsedListing."""
        car_info = offer.get("vehicle_info", offer.get("car_info", {}))
        mark_info = car_info.get("mark_info", {})
        model_info = car_info.get("model_info", {})
        tech_param = car_info.get("tech_param", {})
        configuration = car_info.get("configuration", {})

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

        # Description
        description = offer.get("description") or offer.get("seller_comment") or ""

        # VIN and documents
        vin = docs.get("vin")
        pts_type = docs.get("pts") or docs.get("pts_type")
        owners_count = docs.get("owners_number") or docs.get("owners_count")
        custom_cleared = docs.get("custom_cleared")

        # Tech specs — from tech_param or top-level car_info
        engine_type = tech_param.get("engine_type") or car_info.get("engine_type")
        transmission = tech_param.get("transmission") or car_info.get("transmission")
        gear_type = tech_param.get("gear_type") or car_info.get("drive")
        displacement = tech_param.get("displacement") or car_info.get("engine_volume")
        power = tech_param.get("power") or tech_param.get("horse_power")

        engine_vol = None
        if displacement:
            cc = int(displacement)
            engine_vol = round(cc / 1000, 1) if cc > 100 else float(cc)

        body_type = configuration.get("body_type") or car_info.get("body_type")
        steering_wheel = car_info.get("steering_wheel")

        # Color
        color_hex = car_info.get("color_hex")
        color_obj = offer.get("color", {})
        color = None
        if isinstance(color_obj, dict) and color_obj.get("name"):
            color = color_obj["name"]
        elif color_hex:
            color = _hex_to_color_name(str(color_hex))

        # Seller
        seller_info = offer.get("seller", {})
        seller_name = seller_info.get("name") if isinstance(seller_info, dict) else None
        seller_type = offer.get("seller_type")
        is_dealer = seller_type in ("COMMERCIAL", "DEALER") if seller_type else False

        # Dates
        add_info = offer.get("additional_info", {})
        creation_date = add_info.get("creation_date") or offer.get("created")
        listing_date = _parse_autoru_date(str(creation_date)) if creation_date else None

        # Location
        region_info = offer.get("seller", {})
        location = region_info.get("location", {}) if isinstance(region_info, dict) else {}
        region_info_obj = location.get("region_info", {})
        city = location.get("city") or region_info_obj.get("name")
        region = region_info_obj.get("name")

        # Generation
        gen = car_info.get("super_gen", {})
        generation = gen.get("name") if isinstance(gen, dict) else None

        return ParsedListing(
            source="autoru",
            external_id=offer_id,
            brand=brand,
            model=model,
            year=year,
            price=price,
            url=url,
            mileage=mileage,
            description=description or None,
            photos=[p for p in photos if p],
            engine_type=engine_type,
            engine_volume=engine_vol,
            power_hp=int(power) if power else None,
            transmission=transmission,
            drive_type=gear_type,
            body_type=body_type,
            color=color,
            vin=vin,
            steering_wheel=steering_wheel,
            owners_count=int(owners_count) if owners_count else None,
            pts_type=pts_type,
            customs_cleared=custom_cleared,
            seller_type=seller_type,
            seller_name=seller_name,
            is_dealer=is_dealer,
            listing_date=listing_date,
            city=city,
            region=region,
            generation=generation,
            photo_count=len(photos),
        )
