"""Deduplication service for listings.

Detects duplicates across Avito and Auto.ru via:
1. VIN match (exact)
2. Fuzzy match: same brand + model + year, mileage ±5000 km, price ±10%

Within each duplicate group the oldest listing (smallest created_at) becomes
the canonical record; the rest are marked is_duplicate=True / canonical_id=<uuid>.
"""

from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.listing import Listing

# Fuzzy-match tolerances
_MILEAGE_TOLERANCE_KM = 5_000
_PRICE_TOLERANCE_PCT = 0.10


async def detect_and_mark_duplicates(session: AsyncSession) -> int:
    """Find duplicate listings and persist is_duplicate / canonical_id flags.

    Returns the total number of listings newly marked as duplicates.
    """
    result = await session.execute(select(Listing).order_by(Listing.created_at.asc()))
    listings: list[Listing] = list(result.scalars().all())

    # --- group by VIN (non-null, non-empty) ---
    vin_groups: dict[str, list[Listing]] = defaultdict(list)
    no_vin: list[Listing] = []
    for listing in listings:
        vin = (listing.vin or "").strip().upper()
        if vin:
            vin_groups[vin].append(listing)
        else:
            no_vin.append(listing)

    # --- fuzzy-match among listings without VIN ---
    fuzzy_groups: list[list[Listing]] = _fuzzy_group(no_vin)

    # Combine all groups that have > 1 member
    all_groups: list[list[Listing]] = [g for g in list(vin_groups.values()) + fuzzy_groups if len(g) > 1]

    # Flatten to a set of ids already processed
    marked: set[uuid.UUID] = set()
    newly_marked = 0

    for group in all_groups:
        # Sort oldest first → first entry is canonical
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


def _fuzzy_group(listings: list[Listing]) -> list[list[Listing]]:
    """Group listings by brand/model/year + mileage/price tolerance (greedy)."""
    groups: list[list[Listing]] = []
    visited: set[uuid.UUID] = set()

    for i, a in enumerate(listings):
        if a.id in visited:
            continue
        group = [a]
        for b in listings[i + 1 :]:
            if b.id in visited:
                continue
            if _is_fuzzy_match(a, b):
                group.append(b)
                visited.add(b.id)
        if len(group) > 1:
            visited.add(a.id)
            groups.append(group)

    return groups


def _is_fuzzy_match(a: Listing, b: Listing) -> bool:
    """Return True when two listings look like the same physical car."""
    if a.brand.lower() != b.brand.lower():
        return False
    if a.model.lower() != b.model.lower():
        return False
    if a.year != b.year:
        return False

    # Mileage tolerance
    a_mil = a.mileage or 0
    b_mil = b.mileage or 0
    if abs(a_mil - b_mil) > _MILEAGE_TOLERANCE_KM:
        return False

    # Price tolerance
    avg_price = (a.price + b.price) / 2
    return not (avg_price > 0 and abs(a.price - b.price) / avg_price > _PRICE_TOLERANCE_PCT)


def get_duplicate_ids_for(listing: Listing, all_listings: list[Listing]) -> list[uuid.UUID]:
    """Return UUIDs of all duplicates related to this listing.

    For a canonical listing: returns ids of its duplicates.
    For a duplicate: returns id of canonical + sibling duplicates.
    """
    if listing.is_duplicate and listing.canonical_id is not None:
        canonical_id = listing.canonical_id
        # canonical + siblings
        related = [
            lx.id
            for lx in all_listings
            if lx.id != listing.id and (lx.id == canonical_id or lx.canonical_id == canonical_id)
        ]
        return related

    # This is a canonical → find duplicates pointing to it
    return [lx.id for lx in all_listings if lx.canonical_id == listing.id]
