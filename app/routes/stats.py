from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.listing import Listing, ListingAnalysis

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

CATEGORY_LABELS = {
    "clean": "Чистая",
    "damaged_body": "Битая",
    "bad_docs": "Плохие документы",
    "debtor": "Должник",
    "complex_but_profitable": "Сложная, но выгодная",
}


@router.get("/stats", response_class=HTMLResponse)
async def stats_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    # Total listings
    total_result = await session.execute(select(func.count(Listing.id)))
    total = total_result.scalar() or 0

    # Total unique (non-duplicate)
    unique_result = await session.execute(select(func.count(Listing.id)).where(Listing.is_duplicate.is_(False)))
    total_unique = unique_result.scalar() or 0

    # By source (total)
    source_result = await session.execute(select(Listing.source, func.count(Listing.id)).group_by(Listing.source))
    by_source = {row[0]: row[1] for row in source_result.all()}

    # By source (unique only, non-duplicate)
    source_unique_result = await session.execute(
        select(Listing.source, func.count(Listing.id)).where(Listing.is_duplicate.is_(False)).group_by(Listing.source)
    )
    by_source_unique = {row[0]: row[1] for row in source_unique_result.all()}

    # By category (from ListingAnalysis)
    cat_result = await session.execute(
        select(ListingAnalysis.category, func.count(ListingAnalysis.id)).group_by(ListingAnalysis.category)
    )
    by_category = {row[0]: row[1] for row in cat_result.all()}

    # Avg price
    avg_result = await session.execute(select(func.avg(Listing.price)))
    avg_price = avg_result.scalar()

    # Top brands
    brand_result = await session.execute(
        select(
            Listing.brand,
            func.count(Listing.id).label("cnt"),
            func.avg(Listing.price).label("avg_price"),
        )
        .group_by(Listing.brand)
        .order_by(func.count(Listing.id).desc())
        .limit(10)
    )
    top_brands = [
        {"brand": row[0], "count": row[1], "avg_price": round(float(row[2]), 0) if row[2] else 0}
        for row in brand_result.all()
    ]

    # Top cities
    city_result = await session.execute(
        select(
            Listing.city,
            func.count(Listing.id).label("cnt"),
            func.avg(Listing.price).label("avg_price"),
        )
        .where(Listing.city.isnot(None))
        .group_by(Listing.city)
        .order_by(func.count(Listing.id).desc())
        .limit(10)
    )
    top_cities = [
        {"city": row[0], "count": row[1], "avg_price": round(float(row[2]), 0) if row[2] else 0}
        for row in city_result.all()
    ]

    # Coverage metrics
    priced_result = await session.execute(select(func.count(Listing.id)).where(Listing.market_price.isnot(None)))
    total_priced = priced_result.scalar() or 0

    analyzed_result = await session.execute(select(func.count(ListingAnalysis.id)))
    total_analyzed = analyzed_result.scalar() or 0

    hot_deals_result = await session.execute(
        select(func.count(Listing.id)).where(Listing.price_diff_pct > 15, Listing.is_duplicate.is_(False))
    )
    hot_deals = hot_deals_result.scalar() or 0

    dupes_result = await session.execute(select(func.count(Listing.id)).where(Listing.is_duplicate.is_(True)))
    total_dupes = dupes_result.scalar() or 0

    ctx = {
        "request": request,
        "total": total,
        "total_unique": total_unique,
        "total_dupes": total_dupes,
        "total_priced": total_priced,
        "total_analyzed": total_analyzed,
        "hot_deals": hot_deals,
        "priced_pct": round(total_priced / total * 100, 1) if total else 0,
        "analyzed_pct": round(total_analyzed / total_unique * 100, 1) if total_unique else 0,
        "by_source": by_source,
        "by_source_unique": by_source_unique,
        "by_category": by_category,
        "category_labels": CATEGORY_LABELS,
        "avg_price": round(float(avg_price), 0) if avg_price else 0,
        "top_brands": top_brands,
        "top_cities": top_cities,
    }

    return templates.TemplateResponse(request, "stats.html", ctx)
