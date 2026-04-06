"""Real listing checker that queries PostgreSQL for new high-score listings."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.session import async_session_factory
from app.models.listing import Listing as DBListing
from app.models.listing import ListingAnalysis
from bot.services.checker import Listing


class DatabaseChecker:
    """Fetches recent high-score listings from the database.

    Implements the ListingChecker protocol expected by the notifier.
    """

    def __init__(self, min_score: float = 5.0, lookback_minutes: int = 30):
        self._min_score = min_score
        self._lookback_minutes = lookback_minutes
        self._last_check: datetime | None = None

    async def fetch_new(self) -> Sequence[Listing]:
        cutoff = self._last_check or (datetime.now(UTC) - timedelta(minutes=self._lookback_minutes))
        self._last_check = datetime.now(UTC)

        async with async_session_factory() as session:
            stmt = (
                select(DBListing, ListingAnalysis)
                .join(ListingAnalysis, DBListing.id == ListingAnalysis.listing_id)
                .where(
                    ListingAnalysis.score >= self._min_score,
                    DBListing.created_at >= cutoff,
                    DBListing.is_duplicate.is_(False),
                )
                .order_by(ListingAnalysis.score.desc())
                .limit(20)
            )
            result = await session.execute(stmt)
            rows = result.all()

        listings = []
        for db_listing, analysis in rows:
            discount_pct = float(db_listing.price_diff_pct) if db_listing.price_diff_pct else 0.0
            market_price = db_listing.market_price or db_listing.price

            photo_url = None
            if db_listing.photos and isinstance(db_listing.photos, list) and db_listing.photos:
                photo_url = db_listing.photos[0]

            listings.append(
                Listing(
                    brand=db_listing.brand,
                    model=db_listing.model,
                    year=db_listing.year,
                    price=db_listing.price,
                    market_price=market_price,
                    discount_pct=round(discount_pct, 1),
                    category=analysis.category,
                    url=db_listing.url,
                    photo_url=photo_url,
                )
            )

        return listings
