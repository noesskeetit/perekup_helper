"""Listing source integration.

This module defines the interface that a concrete listing source must implement
and provides two implementations:

* ``DemoChecker`` — returns synthetic data for end-to-end testing.
* ``DatabaseChecker`` — queries the main app PostgreSQL database for real listings.
"""

from __future__ import annotations

import dataclasses
import random
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import joinedload

from app.models.listing import Listing as AppListing


@dataclasses.dataclass(frozen=True)
class Listing:
    brand: str
    model: str
    year: int
    price: float
    market_price: float
    discount_pct: float
    category: str
    url: str
    photo_url: str | None = None
    deal_score: float | None = None
    mileage: int | None = None
    city: str | None = None
    source: str | None = None
    listing_id: str | None = None


class ListingChecker(Protocol):
    async def fetch_new(self) -> Sequence[Listing]: ...


class DatabaseChecker:
    """Fetches real listings from the main app database (listings + listing_analysis)."""

    def __init__(
        self,
        db_url: str | None = None,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ):
        if session_factory is not None:
            self._session_factory = session_factory
        elif db_url is not None:
            engine = create_async_engine(db_url, echo=False)
            self._session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        else:
            raise ValueError("Either db_url or session_factory must be provided")
        self._last_check: datetime | None = None

    async def fetch_new(
        self,
        *,
        brand: str | None = None,
        model: str | None = None,
        max_price: float | None = None,
        min_discount: float | None = None,
    ) -> Sequence[Listing]:
        async with self._session_factory() as session:
            stmt = select(AppListing).where(AppListing.is_duplicate.is_(False)).options(joinedload(AppListing.analysis))

            if self._last_check is not None:
                stmt = stmt.where(AppListing.created_at > self._last_check)

            if brand is not None:
                stmt = stmt.where(func.lower(AppListing.brand) == brand.lower())

            if model is not None:
                stmt = stmt.where(func.lower(AppListing.model) == model.lower())

            if max_price is not None:
                stmt = stmt.where(AppListing.price <= max_price)

            if min_discount is not None:
                # price_diff_pct is negative (e.g. -10.0 means 10% below market)
                stmt = stmt.where(AppListing.price_diff_pct <= -min_discount)

            result = await session.execute(stmt)
            rows = result.scalars().unique().all()

            self._last_check = datetime.now(UTC)

            return [self._to_listing(row) for row in rows]

    @staticmethod
    def _to_listing(db_listing: AppListing) -> Listing:
        category = ""
        if db_listing.analysis:
            cat = db_listing.analysis.category
            category = cat.value if hasattr(cat, "value") else str(cat)

        photo_url = None
        if db_listing.photos:
            photo_url = db_listing.photos[0]

        discount_pct = abs(float(db_listing.price_diff_pct)) if db_listing.price_diff_pct else 0.0

        return Listing(
            brand=db_listing.brand,
            model=db_listing.model,
            year=db_listing.year,
            price=float(db_listing.price),
            market_price=float(db_listing.market_price) if db_listing.market_price else float(db_listing.price),
            discount_pct=round(discount_pct, 1),
            category=category,
            url=db_listing.url,
            photo_url=photo_url,
            deal_score=float(db_listing.deal_score) if db_listing.deal_score else None,
            mileage=db_listing.mileage,
            city=db_listing.city,
            source=db_listing.source,
            listing_id=str(db_listing.id),
        )


class DemoChecker:
    """Returns a small batch of random demo listings for testing."""

    _BRANDS = [
        ("Toyota", "Camry"),
        ("Toyota", "RAV4"),
        ("BMW", "X5"),
        ("BMW", "3 Series"),
        ("Mercedes-Benz", "E-Class"),
        ("Kia", "K5"),
        ("Hyundai", "Tucson"),
        ("Volkswagen", "Tiguan"),
    ]

    async def fetch_new(self) -> Sequence[Listing]:
        listings: list[Listing] = []
        for _ in range(random.randint(1, 4)):
            brand, model = random.choice(self._BRANDS)
            market_price = random.randint(800_000, 5_000_000)
            discount = random.uniform(3, 25)
            price = market_price * (1 - discount / 100)
            year = random.randint(2018, 2025)
            listings.append(
                Listing(
                    brand=brand,
                    model=model,
                    year=year,
                    price=round(price),
                    market_price=market_price,
                    discount_pct=round(discount, 1),
                    category=random.choice(["Седан", "Кроссовер", "Универсал", "Хэтчбек"]),
                    url=f"https://example.com/listing/{random.randint(100000, 999999)}",
                    photo_url=None,
                )
            )
        return listings
