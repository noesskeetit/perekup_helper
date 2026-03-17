from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class SortBy(str, Enum):
    price_diff = "price_diff"
    score = "score"
    created_at = "created_at"


class ListingBase(BaseModel):
    brand: str
    model: str
    year: int
    price: float
    mileage: int
    market_price: Optional[float] = None
    price_diff: Optional[float] = None
    market_diff_pct: Optional[float] = None
    score: Optional[float] = None
    category: Optional[str] = None
    source_url: Optional[str] = None
    image_url: Optional[str] = None


class ListingResponse(ListingBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ListingDetailResponse(ListingResponse):
    ai_analysis: Optional[str] = None


class PaginatedListings(BaseModel):
    items: List[ListingResponse]
    total: int
    page: int
    per_page: int
    pages: int


class ListingsFilter(BaseModel):
    brand: Optional[str] = None
    model_name: Optional[str] = Field(None, alias="model")
    year_from: Optional[int] = None
    year_to: Optional[int] = None
    price_from: Optional[float] = None
    price_to: Optional[float] = None
    mileage_from: Optional[int] = None
    mileage_to: Optional[int] = None
    market_diff_pct: Optional[float] = None
    category: Optional[str] = None
    sort_by: SortBy = SortBy.created_at
    page: int = Field(1, ge=1)
    per_page: int = Field(20, ge=1, le=100)


class StatsResponse(BaseModel):
    total_listings: int
    avg_price: Optional[float] = None
    avg_mileage: Optional[float] = None
    avg_market_diff_pct: Optional[float] = None
    avg_score: Optional[float] = None
    by_category: Dict[str, int] = {}
    by_brand: Dict[str, int] = {}
