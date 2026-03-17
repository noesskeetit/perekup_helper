from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.sync_listing import SyncListing as Listing
from app.schemas import StatsResponse

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
