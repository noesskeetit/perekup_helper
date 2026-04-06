"""K-NN comparable sales module for used car pricing.

Finds the K most similar recently-listed cars and derives a price estimate
from their actual asking prices.  No ML involved — just database lookups,
distance calculations, and simple percentile statistics.

This is the most reliable pricing method for rare cars where the CatBoost
model lacks enough training samples.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session_factory
from app.models.listing import Listing

logger = logging.getLogger(__name__)

# ── Distance weights ────────────────────────────────────────────────
W_YEAR = 0.35
W_MILEAGE = 0.45
W_ENGINE = 0.20

# ── Default ranges for normalisation ────────────────────────────────
YEAR_NORM = 2  # ±2 years  → diff / 2
MILEAGE_LOG_SCALE = True  # use log-scale for mileage diff
ENGINE_NORM = 0.5  # ±0.5 L  → diff / 0.5


def _normalised_distance(
    target_year: int,
    target_mileage: int,
    target_engine: float,
    cand_year: int,
    cand_mileage: int,
    cand_engine: float,
) -> float:
    """Compute weighted distance between a target listing and a candidate.

    Distance formula
    ----------------
    distance = w_year  * (year_diff / 2)²
             + w_mileage * (log_mileage_diff)²
             + w_engine  * (engine_diff / 0.5)²

    All components are ≥ 0.  Lower is more similar.
    """
    # Year component
    year_diff = abs(target_year - cand_year) / YEAR_NORM
    year_component = W_YEAR * year_diff**2

    # Mileage component (log-scale)
    t_mil = max(target_mileage, 1)
    c_mil = max(cand_mileage, 1)
    log_mileage_diff = abs(math.log(t_mil) - math.log(c_mil))
    mileage_component = W_MILEAGE * log_mileage_diff**2

    # Engine volume component
    engine_diff = abs(target_engine - cand_engine) / ENGINE_NORM
    engine_component = W_ENGINE * engine_diff**2

    return year_component + mileage_component + engine_component


async def find_comparables(
    listing: dict[str, Any],
    k: int = 10,
    max_age_days: int = 60,
) -> list[dict[str, Any]]:
    """Find *k* most similar non-duplicate listings from the database.

    Parameters
    ----------
    listing : dict
        Must contain at least ``brand``, ``model``, ``year``.
        Optional: ``mileage``, ``engine_volume``.
    k : int
        Number of neighbours to return.
    max_age_days : int
        Only consider listings created within this many days.

    Returns
    -------
    list[dict]
        Sorted by distance (ascending).  Each dict contains:
        ``price``, ``year``, ``mileage``, ``source``, ``distance``.
    """
    brand: str = listing["brand"]
    model: str = listing["model"]
    target_year: int = int(listing["year"])
    target_mileage: int = int(listing.get("mileage") or 0)
    target_engine: float = float(listing.get("engine_volume") or 0.0)

    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)

    async with async_session_factory() as session:
        candidates = await _query_candidates(
            session,
            brand=brand,
            model=model,
            target_year=target_year,
            target_mileage=target_mileage,
            cutoff=cutoff,
        )

    # Score every candidate and keep the top-k
    scored: list[dict[str, Any]] = []
    for row in candidates:
        dist = _normalised_distance(
            target_year=target_year,
            target_mileage=target_mileage,
            target_engine=target_engine,
            cand_year=row["year"],
            cand_mileage=row["mileage"],
            cand_engine=row["engine_volume"],
        )
        scored.append(
            {
                "price": row["price"],
                "year": row["year"],
                "mileage": row["mileage"],
                "source": row["source"],
                "distance": round(dist, 6),
            }
        )

    scored.sort(key=lambda x: x["distance"])
    return scored[:k]


async def _query_candidates(
    session: AsyncSession,
    *,
    brand: str,
    model: str,
    target_year: int,
    target_mileage: int,
    cutoff: datetime,
) -> list[dict[str, Any]]:
    """Fetch candidate rows from the database.

    Filters applied server-side:
    - exact brand match (case-insensitive)
    - exact model match (case-insensitive)
    - non-duplicate listings only
    - created within *cutoff*
    - price > 0
    - year within ±2 of target
    - mileage within ±30 000 km of target (or NULL mileage allowed)
    """
    year_lo = target_year - 2
    year_hi = target_year + 2
    mileage_lo = max(0, target_mileage - 30_000)
    mileage_hi = target_mileage + 30_000

    stmt = (
        select(
            Listing.price,
            Listing.year,
            Listing.mileage,
            Listing.engine_volume,
            Listing.source,
        )
        .where(
            Listing.is_duplicate.is_(False),
            Listing.created_at >= cutoff,
            Listing.price > 0,
            func.lower(Listing.brand) == brand.lower(),
            func.lower(Listing.model) == model.lower(),
            Listing.year.between(year_lo, year_hi),
        )
        .where(
            # mileage NULL is acceptable (we'll treat as 0 in distance calc)
            (Listing.mileage.is_(None)) | (Listing.mileage.between(mileage_lo, mileage_hi))
        )
    )

    result = await session.execute(stmt)
    rows = result.all()

    return [
        {
            "price": r.price,
            "year": r.year,
            "mileage": r.mileage or 0,
            "engine_volume": r.engine_volume or 0.0,
            "source": r.source,
        }
        for r in rows
    ]


def compute_comparable_price(comparables: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive pricing statistics from a set of comparable listings.

    Returns
    -------
    dict
        ``median_price`` (P50), ``p25_price``, ``p75_price``,
        ``count``, ``confidence`` (0.2 – 1.0).
    """
    count = len(comparables)

    if count == 0:
        return {
            "median_price": None,
            "p25_price": None,
            "p75_price": None,
            "count": 0,
            "confidence": 0.0,
        }

    prices = sorted(c["price"] for c in comparables)

    median_price = _percentile(prices, 50)
    p25_price = _percentile(prices, 25)
    p75_price = _percentile(prices, 75)

    # Confidence: linear scale from 0.2 (1 comparable) to 1.0 (≥10 comparables)
    if count >= 10:
        confidence = 1.0
    else:
        confidence = round(0.2 + 0.8 * (count - 1) / 9, 2)

    return {
        "median_price": int(median_price),
        "p25_price": int(p25_price),
        "p75_price": int(p75_price),
        "count": count,
        "confidence": confidence,
    }


# ── helpers ─────────────────────────────────────────────────────────


def _percentile(sorted_values: list[int | float], pct: int) -> float:
    """Compute the *pct*-th percentile using linear interpolation.

    *sorted_values* must already be sorted in ascending order.
    """
    n = len(sorted_values)
    if n == 1:
        return float(sorted_values[0])

    k = (pct / 100) * (n - 1)
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    frac = k - lo

    if lo == hi:
        return float(sorted_values[lo])
    return sorted_values[lo] + frac * (sorted_values[hi] - sorted_values[lo])
