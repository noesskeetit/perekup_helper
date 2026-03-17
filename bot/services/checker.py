"""Stub module for listing source integration.

This module defines the interface that a concrete listing source must implement.
A real implementation would scrape or call an API (Avito, Auto.ru, etc.) and
return ``Listing`` objects.  The ``DemoChecker`` returns synthetic data so the
bot can be tested end-to-end without a live source.
"""

import dataclasses
import random
from typing import List, Optional, Protocol, Sequence


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
    photo_url: Optional[str] = None


class ListingChecker(Protocol):
    async def fetch_new(self) -> Sequence[Listing]: ...


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
        listings: List[Listing] = []
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
                    url="https://example.com/listing/{}".format(random.randint(100000, 999999)),
                    photo_url=None,
                )
            )
        return listings
