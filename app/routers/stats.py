from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.sync_listing import SyncListing as Listing
from app.schemas import (
    BrandStatsItem,
    PriceBucket,
    PriceDistributionResponse,
    StatsResponse,
    StatsSummaryResponse,
)

router = APIRouter(tags=["stats"])


@router.get("/stats", response_model=StatsResponse)
def get_stats(db: Session = Depends(get_db)) -> StatsResponse:
    total = db.query(func.count(Listing.id)).scalar() or 0

    if total == 0:
        return StatsResponse(total_listings=0)

    avg_price = db.query(func.avg(Listing.price)).scalar()
    avg_mileage = db.query(func.avg(Listing.mileage)).scalar()
    avg_market_diff_pct = db.query(func.avg(Listing.market_diff_pct)).scalar()
    avg_score = db.query(func.avg(Listing.score)).scalar()

    category_rows = (
        db.query(Listing.category, func.count(Listing.id))
        .filter(Listing.category.isnot(None))
        .group_by(Listing.category)
        .all()
    )
    by_category = {row[0]: row[1] for row in category_rows}

    brand_rows = (
        db.query(Listing.brand, func.count(Listing.id))
        .group_by(Listing.brand)
        .order_by(func.count(Listing.id).desc())
        .limit(20)
        .all()
    )
    by_brand = {row[0]: row[1] for row in brand_rows}

    return StatsResponse(
        total_listings=total,
        avg_price=round(avg_price, 2) if avg_price else None,
        avg_mileage=round(avg_mileage, 2) if avg_mileage else None,
        avg_market_diff_pct=round(avg_market_diff_pct, 2) if avg_market_diff_pct else None,
        avg_score=round(avg_score, 2) if avg_score else None,
        by_category=by_category,
        by_brand=by_brand,
    )


@router.get("/api/stats/summary", response_model=StatsSummaryResponse)
def get_stats_summary(db: Session = Depends(get_db)) -> StatsSummaryResponse:
    total = db.query(func.count(Listing.id)).scalar() or 0

    if total == 0:
        return StatsSummaryResponse(total_listings=0, total_unique=0)

    total_unique = db.query(func.count(Listing.id)).filter(Listing.is_duplicate.is_(False)).scalar() or 0

    source_rows = db.query(Listing.source, func.count(Listing.id)).group_by(Listing.source).all()
    by_source = {row[0]: row[1] for row in source_rows}

    category_rows = (
        db.query(Listing.category, func.count(Listing.id))
        .filter(Listing.category.isnot(None))
        .group_by(Listing.category)
        .all()
    )
    by_category = {row[0]: row[1] for row in category_rows}

    avg_price = db.query(func.avg(Listing.price)).scalar()

    prices = [row[0] for row in db.query(Listing.price).order_by(Listing.price).all()]
    n = len(prices)
    median_price = float(prices[n // 2]) if n % 2 == 1 else (float(prices[n // 2 - 1]) + float(prices[n // 2])) / 2

    neg_rows = db.query(func.avg(Listing.market_diff_pct)).filter(Listing.market_diff_pct < 0).scalar()
    avg_discount = round(float(neg_rows), 2) if neg_rows is not None else None

    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())

    listings_today = db.query(func.count(Listing.id)).filter(Listing.created_at >= today_start).scalar() or 0
    listings_this_week = db.query(func.count(Listing.id)).filter(Listing.created_at >= week_start).scalar() or 0

    return StatsSummaryResponse(
        total_listings=total,
        total_unique=total_unique,
        by_source=by_source,
        by_category=by_category,
        avg_price=round(float(avg_price), 2) if avg_price is not None else None,
        median_price=round(median_price, 2),
        avg_discount=avg_discount,
        listings_today=listings_today,
        listings_this_week=listings_this_week,
    )


@router.get("/api/stats/brands", response_model=list[BrandStatsItem])
def get_stats_brands(db: Session = Depends(get_db)) -> list[BrandStatsItem]:
    rows = (
        db.query(
            Listing.brand,
            func.count(Listing.id).label("cnt"),
            func.avg(Listing.price).label("avg_price"),
            func.avg(Listing.market_diff_pct).label("avg_discount"),
        )
        .group_by(Listing.brand)
        .order_by(func.count(Listing.id).desc())
        .limit(10)
        .all()
    )
    return [
        BrandStatsItem(
            brand=row.brand,
            count=row.cnt,
            avg_price=round(float(row.avg_price), 2) if row.avg_price is not None else None,
            avg_discount=round(float(row.avg_discount), 2) if row.avg_discount is not None else None,
        )
        for row in rows
    ]


@router.get("/api/stats/price-distribution", response_model=PriceDistributionResponse)
def get_price_distribution(db: Session = Depends(get_db)) -> PriceDistributionResponse:
    prices = [row[0] for row in db.query(Listing.price).all()]
    if not prices:
        return PriceDistributionResponse(buckets=[])

    bucket_size = 100_000
    buckets: dict[int, int] = {}
    for price in prices:
        bucket_idx = int(price) // bucket_size
        buckets[bucket_idx] = buckets.get(bucket_idx, 0) + 1

    result = []
    for idx in sorted(buckets.keys()):
        low = idx * bucket_size // 1000
        high = (idx + 1) * bucket_size // 1000
        result.append(PriceBucket(range=f"{low}-{high}k", count=buckets[idx]))

    return PriceDistributionResponse(buckets=result)
