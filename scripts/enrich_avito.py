"""Enrich Avito listings with detail page data (VIN, engine, drive, etc.).

Uses SOCKS5 proxy and spfa.ru cookies. Changes IP on rate limits.
Commits in batches for reliability.
"""

import asyncio
import sys
import time

sys.path.insert(0, ".")
sys.path.insert(0, "avipars")

BATCH_COMMIT = 20
PAUSE = 2.0


async def main():
    from dotenv import load_dotenv

    load_dotenv()

    from load_config import load_avito_config
    from parser.cookies.factory import build_cookies_provider
    from parser.http.client import HttpClient
    from parser.proxies.proxy_factory import build_proxy
    from sqlalchemy import select

    from app.db.session import async_session_factory, engine
    from app.models.listing import Listing
    from app.parsers.avito_detail import enrich_listing_from_detail
    from app.parsers.base import ParsedListing

    config = load_avito_config("avipars/config.toml")
    proxy = build_proxy(config)
    cookies = build_cookies_provider(config)
    http = HttpClient(proxy=proxy, cookies=cookies, timeout=30, max_retries=5, retry_delay=10)

    # Find listings needing enrichment: no description AND no VIN AND no engine_type
    async with async_session_factory() as session:
        stmt = (
            select(Listing)
            .where(
                Listing.source == "avito",
                Listing.vin.is_(None),
            )
            .order_by(Listing.created_at.desc())
            .limit(500)
        )
        result = await session.execute(stmt)
        listings = list(result.scalars().all())

    total = len(listings)
    print(f"Enriching {total} listings...")

    enriched = 0
    errors = 0
    blocked = 0

    for i, db_listing in enumerate(listings):
        if not db_listing.url:
            continue

        try:
            resp = http.request("GET", db_listing.url)
            html = resp.text
            if not html or len(html) < 5000:
                errors += 1
                continue

            # Create temp ParsedListing to extract params
            temp = ParsedListing(
                source="avito",
                external_id=db_listing.external_id,
                brand=db_listing.brand,
                model=db_listing.model,
                year=db_listing.year,
                price=db_listing.price,
                url=db_listing.url,
            )
            enrich_listing_from_detail(temp, html)

            # Update DB
            async with async_session_factory() as session:
                db_obj = await session.get(Listing, db_listing.id)
                if db_obj:
                    for field in [
                        "mileage",
                        "engine_type",
                        "engine_volume",
                        "power_hp",
                        "transmission",
                        "drive_type",
                        "body_type",
                        "color",
                        "vin",
                        "owners_count",
                        "description",
                        "steering_wheel",
                        "condition",
                        "pts_type",
                        "customs_cleared",
                        "generation",
                        "modification",
                        "seller_type",
                        "seller_name",
                        "region",
                        "listing_date",
                        "is_dealer",
                        "photo_count",
                    ]:
                        val = getattr(temp, field, None)
                        if val is not None and getattr(db_obj, field, None) is None:
                            setattr(db_obj, field, val)
                    await session.commit()
                    enriched += 1

        except Exception as e:
            errors += 1
            err_str = str(e)
            if "429" in err_str or "403" in err_str:
                blocked += 1
                if blocked >= 3:
                    print(f"  Too many blocks ({blocked}), changing IP...")
                    from app.parsers.proxy_manager import change_ip

                    change_ip()
                    blocked = 0

        if (i + 1) % 20 == 0:
            print(f"  Progress: {i + 1}/{total} (enriched={enriched}, errors={errors})")

        time.sleep(PAUSE)

    print(f"\nDone: enriched={enriched}, errors={errors}, total={total}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
