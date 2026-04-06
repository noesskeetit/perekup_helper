"""Base parser interface and common types for all listing sources."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ParsedListing:
    """Unified listing data from any source."""

    source: str
    external_id: str
    brand: str
    model: str
    year: int
    price: int
    url: str
    mileage: int | None = None
    description: str | None = None
    photos: list[str] = field(default_factory=list)
    city: str | None = None
    vin: str | None = None
    engine_type: str | None = None  # бензин, дизель, гибрид, электро
    engine_volume: float | None = None  # литры
    power_hp: int | None = None
    transmission: str | None = None  # МКПП, АКПП, вариатор, робот
    drive_type: str | None = None  # передний, задний, полный
    body_type: str | None = None  # седан, кроссовер, хэтчбек
    color: str | None = None
    owners_count: int | None = None
    steering_wheel: str | None = None
    condition: str | None = None
    generation: str | None = None
    modification: str | None = None
    seller_type: str | None = None
    seller_name: str | None = None
    region: str | None = None
    listing_date: str | None = None  # ISO format
    is_dealer: bool = False
    pts_type: str | None = None
    customs_cleared: bool | None = None
    photo_count: int = 0
    raw_data: dict | None = None


@dataclass
class ParseResult:
    """Summary of a single parse run."""

    source: str
    total_fetched: int = 0
    new_saved: int = 0
    duplicates_skipped: int = 0
    errors: int = 0
    elapsed_seconds: float = 0.0
    pages_fetched: int = 0
    captchas_hit: int = 0

    @property
    def listings_per_ban(self) -> float | None:
        """Key metric: how many listings we get per captcha/ban event."""
        if self.captchas_hit == 0:
            return None  # No bans — infinite efficiency
        return self.total_fetched / self.captchas_hit


class BaseParser(ABC):
    """Interface that all source parsers must implement."""

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Short identifier: 'avito', 'drom', 'autoru'."""

    @abstractmethod
    async def fetch_listings(self) -> list[ParsedListing]:
        """Fetch listings from the source. Returns normalized data."""
