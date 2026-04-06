"""Avito parser adapter — wraps AviPars to produce ParsedListing objects."""

from __future__ import annotations

import asyncio
import logging
import re
import threading

from app.parsers.base import BaseParser, ParsedListing

logger = logging.getLogger(__name__)


def _extract_brand_model(title: str) -> tuple[str, str]:
    """Best-effort extraction of brand and model from an Avito listing title.

    Avito titles for cars typically look like:
      'Toyota Camry 2.5 AT, 2019, 45 000 km'
      'LADA (ВАЗ) Granta 1.6 MT, 2020'
    """
    # Strip mileage/year suffixes
    clean = re.split(r",\s*\d{4}", title)[0]
    clean = re.split(r"\d+\.\d+\s*(AT|MT|CVT|AMT)", clean)[0].strip()

    # Handle "Brand (Alias) Model" pattern — keep parenthetical with brand
    paren_match = re.match(r"^(.+?\([^)]+\))\s+(.*)", clean)
    if paren_match:
        brand = paren_match.group(1).strip()
        model = paren_match.group(2).strip()
        return brand, model

    parts = clean.split(None, 1)
    brand = parts[0] if parts else title
    model = parts[1] if len(parts) > 1 else ""
    return brand.strip(), model.strip()


def _extract_year(title: str) -> int:
    """Extract 4-digit year from title like 'Toyota Camry 2.5 AT, 2019, ...'."""
    m = re.search(r"\b(19|20)\d{2}\b", title)
    return int(m.group()) if m else 0


def _first_photo_url(ad) -> list[str]:
    """Extract photo URLs from an AviPars Item object."""
    photos = []
    if hasattr(ad, "images") and ad.images:
        for img in ad.images[:5]:
            if hasattr(img, "root") and isinstance(img.root, dict):
                # Take the largest available image
                url = img.root.get("640x480") or img.root.get("208x156") or next(iter(img.root.values()), None)
                if url:
                    photos.append(str(url))
    return photos


class AvitoParser(BaseParser):
    """Wraps the AviPars library to fetch Avito car listings.

    Two-phase approach:
    1. Fetch listing pages via AviPars (fast, gets title/price/id/photos)
    2. Fetch detail pages for each listing (gets mileage, VIN, engine, etc.)
    """

    source_name = "avito"

    def __init__(self, config_path: str = "avipars/config.toml", fetch_details: bool = True, detail_pause: float = 1.5):
        self._config_path = config_path
        self._fetch_details = fetch_details
        self._detail_pause = detail_pause

    async def fetch_listings(self) -> list[ParsedListing]:
        """Run AviPars synchronously in a thread, then convert results."""
        return await asyncio.to_thread(self._run_sync)

    def _run_sync(self) -> list[ParsedListing]:
        import sys
        from pathlib import Path

        # Add avipars to path so its imports work
        avipars_dir = Path(self._config_path).parent.resolve()
        if str(avipars_dir) not in sys.path:
            sys.path.insert(0, str(avipars_dir))

        from load_config import load_avito_config
        from parser_cls import AvitoParse

        config = load_avito_config(self._config_path)
        # Force one-time run
        config.one_time_start = True
        config.save_xlsx = False

        stop_event = threading.Event()
        parser = AvitoParse(config, stop_event=stop_event)

        collected: list = []
        original_save_viewed = parser._AvitoParse__save_viewed

        def capture_and_save(ads):
            collected.extend(ads)
            original_save_viewed(ads)

        parser._AvitoParse__save_viewed = capture_and_save
        parser.parse()

        results = []
        for ad in collected:
            try:
                listing = self._convert_ad(ad)
                if listing:
                    results.append(listing)
            except Exception:
                logger.warning("Failed to convert Avito ad id=%s", getattr(ad, "id", "?"), exc_info=True)

        # Phase 2: Enrich with detail pages
        if self._fetch_details and results:
            results = self._enrich_with_details(results, parser)

        logger.info("AvitoParser fetched %d listings", len(results))
        return results

    def _enrich_with_details(self, listings: list[ParsedListing], avito_parser) -> list[ParsedListing]:
        """Fetch detail pages and extract full specs."""
        import time

        from app.parsers.avito_detail import enrich_listing_from_detail

        logger.info("Enriching %d listings with detail pages", len(listings))
        enriched = 0

        for listing in listings:
            if not listing.url:
                continue
            try:
                html = avito_parser.fetch_data(url=listing.url)
                if html:
                    enrich_listing_from_detail(listing, html)
                    enriched += 1
            except Exception:
                logger.debug("Failed to enrich %s", listing.external_id, exc_info=True)

            time.sleep(self._detail_pause)

        logger.info("Enriched %d/%d listings with detail data", enriched, len(listings))
        return listings

    def _convert_ad(self, ad) -> ParsedListing | None:
        title = getattr(ad, "title", "") or ""
        ad_id = getattr(ad, "id", None)
        if not ad_id or not title:
            return None

        brand, model = _extract_brand_model(title)
        year = _extract_year(title)
        price_obj = getattr(ad, "priceDetailed", None)
        price = price_obj.value if price_obj else 0

        url_path = getattr(ad, "urlPath", "") or ""
        url = f"https://www.avito.ru{url_path}" if url_path else ""

        return ParsedListing(
            source="avito",
            external_id=str(ad_id),
            brand=brand,
            model=model,
            year=year,
            price=price,
            url=url,
            description=getattr(ad, "description", None),
            photos=_first_photo_url(ad),
            city=getattr(ad.location, "name", None) if getattr(ad, "location", None) else None,
            raw_data={"title": title},
        )
