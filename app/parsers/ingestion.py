"""Ingestion service: normalizes and upserts ParsedListing objects into PostgreSQL."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

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

    # Normalize all listings (normalize_listing returns None for garbage data)
    listings = [normed for item in listings if (normed := normalize_listing(item)) is not None]

    async with async_session_factory() as session:
        external_ids = [item.external_id for item in listings]
        stmt = select(Listing).where(
            Listing.source == source,
            Listing.external_id.in_(external_ids),
        )
        rows = await session.execute(stmt)
        existing_by_eid: dict[str, Listing] = {row[0].external_id: row[0] for row in rows}

        new_listings = []
        for parsed in listings:
            existing = existing_by_eid.get(parsed.external_id)
            if existing is not None:
                if parsed.price != existing.price:
                    # Record old price in price_history
                    raw = existing.raw_data or {}
                    history: list[dict] = raw.get("price_history", [])
                    history.append({"price": existing.price, "date": datetime.utcnow().date().isoformat()})
                    raw["price_history"] = history
                    existing.raw_data = raw
                    flag_modified(existing, "raw_data")
                    existing.price = parsed.price
                    result.prices_updated += 1
                    logger.info(
                        "Price change for %s %s: %d -> %d (external_id=%s)",
                        source,
                        parsed.external_id,
                        history[-1]["price"],
                        parsed.price,
                        parsed.external_id,
                    )
                else:
                    result.duplicates_skipped += 1
                # Touch updated_at so we know the listing is still active on the source
                existing.updated_at = datetime.now(UTC)
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
                listing_date=_parse_listing_date(parsed.listing_date),
                is_dealer=parsed.is_dealer,
                pts_type=parsed.pts_type,
                customs_cleared=parsed.customs_cleared,
                photo_count=parsed.photo_count,
                raw_data=parsed.raw_data,
            )
            new_listings.append(listing)

        if new_listings or result.prices_updated:
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
                    logger.warning(
                        "Ingested %d/%d %s listings (%d failed)", saved, len(new_listings), source, result.errors
                    )

            if result.new_saved or result.prices_updated:
                logger.info(
                    "Ingested %d new %s listings (%d duplicates skipped, %d prices updated)",
                    result.new_saved,
                    source,
                    result.duplicates_skipped,
                    result.prices_updated,
                )
        else:
            logger.info("No new %s listings to ingest (%d duplicates)", source, result.duplicates_skipped)

    return result


def _parse_listing_date(iso_str: str | None) -> datetime | None:
    """Convert an ISO date string (e.g. '2024-04-06') to a datetime object."""
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str)
    except (ValueError, TypeError):
        return None
