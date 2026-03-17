from __future__ import annotations

import math
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Listing
from app.schemas import (
    ListingDetailResponse,
    PaginatedListings,
    SortBy,
)

router = APIRouter(tags=["listings"])


@router.get("/listings", response_model=PaginatedListings)
def list_listings(
    brand: Optional[str] = Query(None, description="Фильтр по марке"),
    model: Optional[str] = Query(None, description="Фильтр по модели"),
    year_from: Optional[int] = Query(None, description="Год от"),
    year_to: Optional[int] = Query(None, description="Год до"),
    price_from: Optional[float] = Query(None, description="Цена от"),
    price_to: Optional[float] = Query(None, description="Цена до"),
    mileage_from: Optional[int] = Query(None, description="Пробег от"),
    mileage_to: Optional[int] = Query(None, description="Пробег до"),
    market_diff_pct: Optional[float] = Query(
        None, description="Макс. отклонение от рыночной цены (%)"
    ),
    category: Optional[str] = Query(None, description="Категория чистоты"),
    sort_by: SortBy = Query(SortBy.created_at, description="Сортировка"),
    page: int = Query(1, ge=1, description="Номер страницы"),
    per_page: int = Query(20, ge=1, le=100, description="Элементов на странице"),
    db: Session = Depends(get_db),
) -> PaginatedListings:
    query = db.query(Listing)

    if brand is not None:
        query = query.filter(Listing.brand.ilike(f"%{brand}%"))
    if model is not None:
        query = query.filter(Listing.model.ilike(f"%{model}%"))
    if year_from is not None:
        query = query.filter(Listing.year >= year_from)
    if year_to is not None:
        query = query.filter(Listing.year <= year_to)
    if price_from is not None:
        query = query.filter(Listing.price >= price_from)
    if price_to is not None:
        query = query.filter(Listing.price <= price_to)
    if mileage_from is not None:
        query = query.filter(Listing.mileage >= mileage_from)
    if mileage_to is not None:
        query = query.filter(Listing.mileage <= mileage_to)
    if market_diff_pct is not None:
        query = query.filter(Listing.market_diff_pct <= market_diff_pct)
    if category is not None:
        query = query.filter(Listing.category == category)

    if sort_by == SortBy.score:
        query = query.order_by(Listing.score.desc())
    elif sort_by == SortBy.price_diff:
        query = query.order_by(Listing.price_diff.asc())
    else:
        query = query.order_by(Listing.created_at.desc())

    total = query.count()
    pages = math.ceil(total / per_page) if total > 0 else 0
    items = query.offset((page - 1) * per_page).limit(per_page).all()

    return PaginatedListings(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


@router.get("/listings/{listing_id}", response_model=ListingDetailResponse)
def get_listing(
    listing_id: int,
    db: Session = Depends(get_db),
) -> ListingDetailResponse:
    listing = db.query(Listing).filter(Listing.id == listing_id).first()
    if listing is None:
        raise HTTPException(status_code=404, detail="Объявление не найдено")
    return listing
