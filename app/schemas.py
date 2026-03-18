from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class SortBy(str, Enum):
    price_diff = "price_diff"
    score = "score"
    created_at = "created_at"
    market_diff_pct = "market_diff_pct"


class ListingBase(BaseModel):
    brand: str
    model: str
    year: int
    price: float
    mileage: int
    market_price: float | None = None
    price_diff: float | None = None
    market_diff_pct: float | None = None
    score: float | None = None
    category: str | None = None
    source_url: str | None = None
    image_url: str | None = None
    is_duplicate: bool = False
    canonical_id: int | None = None


class ListingResponse(ListingBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ListingDetailResponse(ListingResponse):
    ai_analysis: str | None = None
    duplicate_ids: list[int] = []


class DuplicateGroup(BaseModel):
    canonical_id: uuid.UUID
    listing_ids: list[uuid.UUID]


class PaginatedListings(BaseModel):
    items: list[ListingResponse]
    total: int
    page: int
    per_page: int
    pages: int


class ListingsFilter(BaseModel):
    brand: str | None = None
    model_name: str | None = Field(None, alias="model")
    year_from: int | None = None
    year_to: int | None = None
    price_from: float | None = None
    price_to: float | None = None
    mileage_from: int | None = None
    mileage_to: int | None = None
    market_diff_pct: float | None = None
    category: str | None = None
    sort_by: SortBy = SortBy.created_at
    page: int = Field(1, ge=1)
    per_page: int = Field(20, ge=1, le=100)


class StatsResponse(BaseModel):
    total_listings: int
    avg_price: float | None = None
    avg_mileage: float | None = None
    avg_market_diff_pct: float | None = None
    avg_score: float | None = None
    by_category: dict[str, int] = {}
    by_brand: dict[str, int] = {}
