"""Backfill Avito listing detail pages for existing DB records.

Finds Avito listings that lack body_type (meaning detail page was never fetched),
then fetches each detail page and updates the DB with enriched data.

Usage:
    python scripts/backfill_avito_details.py [--limit 100] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time

from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.insert(0, ".")

from app.db.session import async_session_factory
from app.parsers.avito_detail import enrich_listing_from_detail
from app.parsers.base import ParsedListing

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _create_avito_session():
    """Create a curl_cffi session for fetching Avito detail pages."""
    import os

    from dotenv import load_dotenv

    load_dotenv()

    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        logger.error("curl_cffi required. Install: pip install curl_cffi")
        sys.exit(1)

    proxy_raw = os.getenv("PROXY_STRING", "")
    proxy_type = os.getenv("PROXY_TYPE", "socks5")
    if proxy_raw and "://" not in proxy_raw:
        proxy = f"{proxy_type}://{proxy_raw}"
    else:
        proxy = proxy_raw or None
    proxies = {"http": proxy, "https": proxy} if proxy else None
    logger.info("Using proxy: %s", proxy_type if proxy else "none")

    session = cffi_requests.Session(impersonate="chrome", proxies=proxies)
    # Warm up cookies
    try:
        resp = session.get("https://www.avito.ru/", timeout=20)
        logger.info("Cookie warmup: status=%d, size=%d", resp.status_code, len(resp.text))
    except Exception as e:
        logger.warning("Cookie warmup failed: %s", e)

    return session


async def get_unenriched_listings(limit: int) -> list[dict]:
    """Find Avito listings without body_type (need detail enrichment)."""
    async with async_session_factory() as session:
        r = await session.execute(
            text("""
            SELECT id, external_id, url, brand, model, year, price,
                   mileage, engine_volume, transmission, engine_type,
                   description, city
            FROM listings
            WHERE source = 'avito'
              AND is_duplicate = false
              AND (body_type IS NULL OR body_type = 'unknown')
              AND url IS NOT NULL
              AND url != ''
            ORDER BY created_at DESC
            LIMIT :limit
        """),
            {"limit": limit},
        )
        rows = r.all()

    results = []
    for r in rows:
        results.append(
            {
                "id": str(r[0]),
                "external_id": r[1],
                "url": r[2],
                "brand": r[3],
                "model": r[4],
                "year": r[5],
                "price": r[6],
                "mileage": r[7],
                "engine_volume": r[8],
                "transmission": r[9],
                "engine_type": r[10],
                "description": r[11],
                "city": r[12],
            }
        )
    return results


async def update_listing(listing_id: str, updates: dict) -> bool:
    """Update a listing with enriched data."""
    if not updates:
        return False

    async with async_session_factory() as session:
        # Build SET clause dynamically
        set_parts = []
        params = {"lid": listing_id}
        for key, value in updates.items():
            set_parts.append(f"{key} = :{key}")
            params[key] = value

        if not set_parts:
            return False

        sql = f"UPDATE listings SET {', '.join(set_parts)} WHERE id = CAST(:lid AS uuid)"
        await session.execute(text(sql), params)
        await session.commit()
        return True


def fetch_and_enrich(session, url: str, listing_dict: dict) -> dict | None:
    """Fetch detail page and extract enrichment data."""
    try:
        resp = session.get(url, timeout=30)
        if resp.status_code != 200:
            logger.debug("HTTP %d for %s", resp.status_code, url)
            return None

        html = resp.text
        if len(html) < 5000:
            logger.debug("Response too small (%d bytes) for %s", len(html), url)
            return None

        # Create a temporary ParsedListing to enrich
        listing = ParsedListing(
            source="avito",
            external_id=listing_dict["external_id"],
            brand=listing_dict["brand"] or "unknown",
            model=listing_dict["model"] or "unknown",
            year=listing_dict["year"] or 2020,
            price=listing_dict["price"] or 0,
            url=url,
        )
        # Preserve existing data
        listing.mileage = listing_dict.get("mileage")
        listing.engine_volume = listing_dict.get("engine_volume")
        listing.transmission = listing_dict.get("transmission")
        listing.engine_type = listing_dict.get("engine_type")
        listing.description = listing_dict.get("description")
        listing.city = listing_dict.get("city")
        listing.raw_data = {}

        enriched = enrich_listing_from_detail(listing, html)

        # Build updates dict (only new non-None fields)
        updates = {}
        field_map = {
            "body_type": enriched.body_type,
            "drive_type": enriched.drive_type,
            "color": enriched.color,
            "engine_type": enriched.engine_type,
            "power_hp": enriched.power_hp,
            "owners_count": enriched.owners_count,
            "vin": enriched.vin,
            "steering_wheel": enriched.steering_wheel,
            "condition": enriched.condition,
            "pts_type": enriched.pts_type,
            "generation": enriched.generation,
            "modification": enriched.modification,
            "seller_type": enriched.seller_type,
            "seller_name": enriched.seller_name,
            "region": enriched.region,
        }

        for field, value in field_map.items():
            if value is not None:
                # Only update if current value is missing
                current = listing_dict.get(field)
                if current is None or current == "" or current == "unknown":
                    updates[field] = value

        # Engine volume / mileage / transmission (update if currently missing)
        if enriched.engine_volume and not listing_dict.get("engine_volume"):
            updates["engine_volume"] = enriched.engine_volume
        if enriched.mileage and not listing_dict.get("mileage"):
            updates["mileage"] = enriched.mileage
        if enriched.transmission and listing_dict.get("transmission") in (None, "", "unknown"):
            updates["transmission"] = enriched.transmission
        if enriched.is_dealer:
            updates["is_dealer"] = True

        return updates if updates else None

    except Exception:
        logger.debug("Error fetching %s", url, exc_info=True)
        return None


async def main():
    parser = argparse.ArgumentParser(description="Backfill Avito detail pages")
    parser.add_argument("--limit", type=int, default=500, help="Max listings to process")
    parser.add_argument("--pause", type=float, default=0.5, help="Pause between requests (seconds)")
    parser.add_argument("--dry-run", action="store_true", help="Don't update DB, just show what would change")
    args = parser.parse_args()

    logger.info("Finding unenriched Avito listings (limit=%d)...", args.limit)
    listings = await get_unenriched_listings(args.limit)
    logger.info("Found %d listings without body_type", len(listings))

    if not listings:
        logger.info("Nothing to backfill!")
        return

    logger.info("Creating Avito session...")
    session = _create_avito_session()

    enriched = 0
    failed = 0
    total_fields = 0
    t0 = time.time()

    for i, listing in enumerate(listings):
        updates = fetch_and_enrich(session, listing["url"], listing)

        if updates:
            enriched += 1
            total_fields += len(updates)
            fields_str = ", ".join(f"{k}={v}" for k, v in list(updates.items())[:5])

            if args.dry_run:
                logger.info(
                    "[%d/%d] DRY RUN %s %s %d: +%d fields (%s)",
                    i + 1,
                    len(listings),
                    listing["brand"],
                    listing["model"],
                    listing["year"],
                    len(updates),
                    fields_str,
                )
            else:
                ok = await update_listing(listing["id"], updates)
                if ok:
                    logger.info(
                        "[%d/%d] Updated %s %s %d: +%d fields (%s)",
                        i + 1,
                        len(listings),
                        listing["brand"],
                        listing["model"],
                        listing["year"],
                        len(updates),
                        fields_str,
                    )
                else:
                    failed += 1
        else:
            failed += 1
            if (i + 1) % 50 == 0:
                logger.info("[%d/%d] Progress: enriched=%d, failed=%d", i + 1, len(listings), enriched, failed)

        time.sleep(args.pause)

    elapsed = time.time() - t0
    logger.info(
        "\nDone! Processed %d listings in %.0fs (%.1f/sec)",
        len(listings),
        elapsed,
        len(listings) / max(elapsed, 1),
    )
    logger.info("Enriched: %d, Failed: %d, Total new fields: %d", enriched, failed, total_fields)
    if enriched > 0:
        logger.info("Avg fields per enriched listing: %.1f", total_fields / enriched)


if __name__ == "__main__":
    asyncio.run(main())
