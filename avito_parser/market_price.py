"""Market price estimator based on median prices in our DB."""

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.listing import Listing

logger = logging.getLogger(__name__)

# Minimum listings needed to calculate reliable median
MIN_SAMPLES = 5

# Max mileage deviation for comparable listings (±30%)
MILEAGE_TOLERANCE = 0.3

# Max year deviation
YEAR_TOLERANCE = 2


async def estimate_market_price(session: AsyncSession, listing: Listing) -> float | None:
    """Estimate market price based on similar listings in DB.

    Finds comparable listings (same brand+model, similar year and mileage)
    and returns the median price as market estimate.
    """
    if not listing.brand or not listing.model:
        return None

    query = select(Listing.price).where(
        func.lower(Listing.brand) == listing.brand.lower(),
        func.lower(Listing.model) == listing.model.lower(),
        Listing.price > 0,
        Listing.id != listing.id,
    )

    # Filter by year range
    if listing.year and listing.year > 1990:
        query = query.where(
            Listing.year >= listing.year - YEAR_TOLERANCE,
            Listing.year <= listing.year + YEAR_TOLERANCE,
        )

    # Filter by mileage range
    if listing.mileage and listing.mileage > 0:
        low = int(listing.mileage * (1 - MILEAGE_TOLERANCE))
        high = int(listing.mileage * (1 + MILEAGE_TOLERANCE))
        query = query.where(
            Listing.mileage >= low,
            Listing.mileage <= high,
        )

    result = await session.execute(query)
    prices = sorted([row[0] for row in result.fetchall() if row[0] and row[0] > 0])

    if len(prices) < MIN_SAMPLES:
        logger.debug(
            "Not enough samples for %s %s %s: %d (need %d)",
            listing.brand,
            listing.model,
            listing.year,
            len(prices),
            MIN_SAMPLES,
        )
        return None

    # Median
    mid = len(prices) // 2
    median = (prices[mid - 1] + prices[mid]) / 2 if len(prices) % 2 == 0 else prices[mid]

    logger.info(
        "Market price for %s %s %s: %.0f (based on %d samples)",
        listing.brand,
        listing.model,
        listing.year,
        median,
        len(prices),
    )
    return float(median)


async def update_market_prices(session: AsyncSession) -> int:
    """Recalculate market_price and price_diff_pct for all listings."""
    result = await session.execute(select(Listing))
    listings = result.scalars().all()
    updated = 0

    for listing in listings:
        market = await estimate_market_price(session, listing)
        if market is None:
            continue

        listing.market_price = market
        if listing.price and listing.price > 0:
            listing.price_diff_pct = round((listing.price - market) / market * 100, 1)
        updated += 1

    await session.commit()
    logger.info("Updated market prices for %d/%d listings", updated, len(listings))
    return updated
