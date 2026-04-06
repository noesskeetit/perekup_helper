"""Ingestion service: normalizes and upserts ParsedListing objects into PostgreSQL."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session_factory
from app.models.listing import Listing
from app.parsers.base import ParsedListing, ParseResult
from app.parsers.normalizer import normalize_listing

logger = logging.getLogger(__name__)


async def ingest_listings(listings: list[ParsedListing], source: str) -> ParseResult:
    """Normalize and upsert parsed listings into the database."""
    result = ParseResult(source=source, total_fetched=len(listings))

    if not listings:
        return result

    # Normalize all listings
    listings = [normalize_listing(l) for l in listings]

    async with async_session_factory() as session:
        external_ids = [l.external_id for l in listings]
        stmt = select(Listing.external_id).where(
            Listing.source == source,
            Listing.external_id.in_(external_ids),
        )
        rows = await session.execute(stmt)
        existing_ids = {row[0] for row in rows}

        new_listings = []
        for parsed in listings:
            if parsed.external_id in existing_ids:
                result.duplicates_skipped += 1
                continue

            listing = Listing(
                source=parsed.source,
                external_id=parsed.external_id,
                brand=parsed.brand,
                model=parsed.model,
                year=parsed.year,
                mileage=parsed.mileage,
                price=parsed.price,
                url=parsed.url,
                description=parsed.description,
                photos=parsed.photos or [],
                vin=parsed.vin,
                engine_type=parsed.engine_type,
                engine_volume=parsed.engine_volume,
                power_hp=parsed.power_hp,
                transmission=parsed.transmission,
                drive_type=parsed.drive_type,
                body_type=parsed.body_type,
                color=parsed.color,
                owners_count=parsed.owners_count,
                city=parsed.city,
                steering_wheel=parsed.steering_wheel,
                condition=parsed.condition,
                generation=parsed.generation,
                modification=parsed.modification,
                seller_type=parsed.seller_type,
                seller_name=parsed.seller_name,
                region=parsed.region,
                is_dealer=parsed.is_dealer,
                pts_type=parsed.pts_type,
                customs_cleared=parsed.customs_cleared,
                photo_count=parsed.photo_count,
                raw_data=parsed.raw_data,
            )
            new_listings.append(listing)

        if new_listings:
            session.add_all(new_listings)
            try:
                await session.commit()
                result.new_saved = len(new_listings)
            except Exception:
                await session.rollback()
                logger.warning("Batch commit failed for %s, retrying one-by-one", source)
                # Retry individually so one bad listing doesn't kill the batch
                saved = 0
                for listing in new_listings:
                    try:
                        async with async_session_factory() as retry_session:
                            retry_session.add(listing)
                            await retry_session.commit()
                            saved += 1
                    except Exception:
                        result.errors += 1
                result.new_saved = saved
                if result.errors:
                    logger.warning("Ingested %d/%d %s listings (%d failed)", saved, len(new_listings), source, result.errors)

            if result.new_saved:
                logger.info(
                    "Ingested %d new %s listings (%d duplicates skipped)",
                    result.new_saved,
                    source,
                    result.duplicates_skipped,
                )
        else:
            logger.info("No new %s listings to ingest (%d duplicates)", source, result.duplicates_skipped)

    return result
