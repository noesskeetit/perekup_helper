"""Cross-source deduplication for listings.

Detects the same car listed on multiple platforms (Avito, Drom, Auto.ru).
Within a single source, duplicates cannot exist — the unique constraint
on (source, external_id) guarantees this at the DB level.

Cross-source matching uses strict fuzzy: exact brand + model + year + city +
price, mileage ±500 km. Conservative thresholds to avoid false positives.
"""

from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.listing import Listing

_MILEAGE_TOLERANCE_KM = 500


async def detect_and_mark_duplicates(session: AsyncSession) -> int:
    """Find cross-source duplicate listings and mark them.

    Only compares listings from DIFFERENT sources. Within the same source,
    each listing has a unique external_id — no deduplication needed.

    Returns the total number of listings newly marked as duplicates.
    """
    result = await session.execute(select(Listing).order_by(Listing.created_at.asc()))
    listings: list[Listing] = list(result.scalars().all())

    # Group by (brand, model, year, city) — only buckets with multiple sources matter
    buckets: dict[tuple[str, str, int, str], list[Listing]] = defaultdict(list)
    for listing in listings:
        if not listing.city:
            continue
        key = (listing.brand.lower(), listing.model.lower(), listing.year, listing.city.lower())
        buckets[key].append(listing)

    marked: set[uuid.UUID] = set()
    newly_marked = 0

    for bucket in buckets.values():
        if len(bucket) < 2:
            continue

        # Check if bucket has listings from multiple sources
        sources = {item.source for item in bucket}
        if len(sources) < 2:
            continue

        # Within this bucket, find cross-source matches
        visited: set[uuid.UUID] = set()
        for i, a in enumerate(bucket):
            if a.id in visited:
                continue
            group = [a]
            for b in bucket[i + 1 :]:
                if b.id in visited:
                    continue
                if _is_cross_source_match(a, b):
                    group.append(b)
                    visited.add(b.id)
            if len(group) > 1:
                visited.add(a.id)
                # Sort oldest first → canonical
                group.sort(key=lambda x: x.created_at)
                canonical = group[0]
                for dup in group[1:]:
                    if dup.id in marked:
                        continue
                    if not dup.is_duplicate:
                        dup.is_duplicate = True
                        dup.canonical_id = canonical.id
                        newly_marked += 1
                    marked.add(dup.id)

    if newly_marked:
        await session.commit()

    return newly_marked


def _is_cross_source_match(a: Listing, b: Listing) -> bool:
    """Return True when two listings from DIFFERENT sources look like the same car."""
    # Must be from different sources
    if a.source == b.source:
        return False

    # Exact match required: brand, model, year
    if a.brand.lower() != b.brand.lower():
        return False
    if a.model.lower() != b.model.lower():
        return False
    if a.year != b.year:
        return False

    # City must match exactly (both must have it)
    if not a.city or not b.city:
        return False
    if a.city.lower() != b.city.lower():
        return False

    # Price must match exactly
    if a.price != b.price:
        return False

    # Mileage must match exactly
    a_mil = a.mileage or 0
    b_mil = b.mileage or 0
    return a_mil == b_mil


def get_duplicate_ids_for(listing: Listing, all_listings: list[Listing]) -> list[uuid.UUID]:
    """Return UUIDs of all duplicates related to this listing."""
    if listing.is_duplicate and listing.canonical_id is not None:
        canonical_id = listing.canonical_id
        return [
            lx.id
            for lx in all_listings
            if lx.id != listing.id and (lx.id == canonical_id or lx.canonical_id == canonical_id)
        ]

    return [lx.id for lx in all_listings if lx.canonical_id == listing.id]
