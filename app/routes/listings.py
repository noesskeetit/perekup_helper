import uuid as _uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_session
from app.models.listing import Listing, ListingAnalysis

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def _listing_age(created_at: datetime | None) -> tuple[str, str]:
    """Return (label, css_class) for a listing's age."""
    if created_at is None:
        return ("—", "age-old")
    now = datetime.now(UTC)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    delta = now - created_at
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return ("< 1ч", "age-fresh")
    if hours < 24:
        return ("< 24ч", "age-recent")
    return ("> 24ч", "age-old")


templates.env.filters["listing_age"] = _listing_age

CATEGORY_LABELS = {
    "clean": "Чистая",
    "damaged_body": "Битая",
    "bad_docs": "Плохие документы",
    "debtor": "Должник",
    "complex_but_profitable": "Сложная, но выгодная",
}

SORT_COLUMNS = {
    "brand": Listing.brand,
    "year": Listing.year,
    "price": Listing.price,
    "market_price": Listing.market_price,
    "price_diff_pct": Listing.price_diff_pct,
    "category": ListingAnalysis.category,
    "confidence": ListingAnalysis.confidence,
    "created_at": Listing.created_at,
    "age": Listing.created_at,
}


async def _get_stats(session: AsyncSession) -> dict:
    """Compute all dashboard stats in a single query."""

    row = (
        await session.execute(
            select(
                func.count().label("total"),
                func.count().filter(Listing.price_diff_pct > 0).label("below_market"),
                func.count().filter(Listing.price_diff_pct > 15).label("hot_deals"),
                func.avg(Listing.price_diff_pct).filter(Listing.price_diff_pct.isnot(None)).label("avg_diff"),
                func.avg(Listing.price).label("avg_price"),
            ).where(Listing.is_duplicate.is_(False))
        )
    ).one()

    model_info = None
    try:
        from app.services.pricing import get_price_model

        pm = get_price_model()
        if pm.is_trained:
            model_info = pm.get_info()
    except Exception:
        pass

    return {
        "total": row.total or 0,
        "below_market": row.below_market or 0,
        "hot_deals": row.hot_deals or 0,
        "avg_diff": float(row.avg_diff) if row.avg_diff else None,
        "avg_price": float(row.avg_price) if row.avg_price else 0,
        "model_info": model_info,
    }


@router.get("/", response_class=HTMLResponse)
async def listings_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
    brand: str | None = Query(None),
    model: str | None = Query(None, alias="car_model"),
    year_from: str | None = Query(None),
    year_to: str | None = Query(None),
    price_from: str | None = Query(None),
    price_to: str | None = Query(None),
    diff_from: str | None = Query(None),
    diff_to: str | None = Query(None),
    market_diff_pct_min: str | None = Query(None),
    category: str | None = Query(None),
    source: str | None = Query(None),
    city: str | None = Query(None),
    hide_duplicates: str = Query("true"),
    sort_by: str = Query("price_diff_pct"),
    sort_dir: str = Query("desc"),
    page: str = Query("1"),
):
    # Parse numeric params safely (empty string → None)
    def _int(v: str | None) -> int | None:
        if not v or not v.strip():
            return None
        try:
            return int(v)
        except ValueError:
            return None

    def _float(v: str | None) -> float | None:
        if not v or not v.strip():
            return None
        try:
            return float(v)
        except ValueError:
            return None

    year_from_v = _int(year_from)
    year_to_v = _int(year_to)
    price_from_v = _int(price_from)
    price_to_v = _int(price_to)
    diff_from_v = _float(diff_from)
    diff_to_v = _float(diff_to)
    market_diff_pct_min_v = _float(market_diff_pct_min)
    page_v = max(1, _int(page) or 1)
    hide_duplicates_v = hide_duplicates.lower() not in ("false", "0", "no")

    per_page = 25
    stmt = select(Listing).options(selectinload(Listing.analysis))

    if brand:
        stmt = stmt.where(Listing.brand.ilike(f"%{brand}%"))
    if model:
        stmt = stmt.where(Listing.model.ilike(f"%{model}%"))
    if year_from_v:
        stmt = stmt.where(Listing.year >= year_from_v)
    if year_to_v:
        stmt = stmt.where(Listing.year <= year_to_v)
    if price_from_v:
        stmt = stmt.where(Listing.price >= price_from_v)
    if price_to_v:
        stmt = stmt.where(Listing.price <= price_to_v)
    if diff_from_v is not None:
        stmt = stmt.where(Listing.price_diff_pct >= diff_from_v)
    if diff_to_v is not None:
        stmt = stmt.where(Listing.price_diff_pct <= diff_to_v)
    if market_diff_pct_min_v is not None and market_diff_pct_min_v > 0:
        stmt = stmt.where(Listing.price_diff_pct >= market_diff_pct_min_v)
    if category:
        stmt = stmt.join(ListingAnalysis, isouter=True).where(ListingAnalysis.category == category)
    if source:
        stmt = stmt.where(Listing.source == source)
    if city:
        stmt = stmt.where(Listing.city.ilike(f"%{city}%"))
    if hide_duplicates_v:
        stmt = stmt.where(Listing.is_duplicate.is_(False))

    needs_join = sort_by in ("category", "confidence") and not category
    if needs_join:
        stmt = stmt.join(ListingAnalysis, isouter=True)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await session.execute(count_stmt)).scalar() or 0

    sort_col = SORT_COLUMNS.get(sort_by, Listing.created_at)
    order_fn = desc if sort_dir == "desc" else asc
    stmt = stmt.order_by(order_fn(sort_col))
    stmt = stmt.offset((page_v - 1) * per_page).limit(per_page)

    result = await session.execute(stmt)
    listings = result.scalars().all()

    total_pages = max(1, (total + per_page - 1) // per_page)

    brands_result = await session.execute(select(Listing.brand).distinct().order_by(Listing.brand))
    brands = [r[0] for r in brands_result.all()]

    cities_result = await session.execute(
        select(Listing.city).where(Listing.city.isnot(None)).distinct().order_by(Listing.city)
    )
    cities = [r[0] for r in cities_result.all()]

    stats = await _get_stats(session)

    cat_result = await session.execute(
        select(ListingAnalysis.category, func.count(ListingAnalysis.id)).group_by(ListingAnalysis.category)
    )
    by_category = {row[0]: row[1] for row in cat_result.all()}

    ctx = {
        "request": request,
        "listings": listings,
        "brands": brands,
        "cities": cities,
        "categories": CATEGORY_LABELS,
        "stats": stats,
        "filters": {
            "brand": brand or "",
            "car_model": model or "",
            "year_from": year_from or "",
            "year_to": year_to or "",
            "price_from": price_from or "",
            "price_to": price_to or "",
            "diff_from": diff_from or "",
            "diff_to": diff_to or "",
            "market_diff_pct_min": market_diff_pct_min or "",
            "category": category or "",
            "source": source or "",
            "city": city or "",
            "hide_duplicates": hide_duplicates_v,
        },
        "sort_by": sort_by,
        "sort_dir": sort_dir,
        "page": page_v,
        "total_pages": total_pages,
        "total": total,
        "avg_price": round(stats["avg_price"], 0) if stats["avg_price"] else 0,
        "avg_discount": round(float(stats["avg_diff"]), 1) if stats["avg_diff"] else 0,
        "by_category": by_category,
    }

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request, "partials/listings_table.html", ctx)
    return templates.TemplateResponse(request, "index.html", ctx)


@router.get("/listings/{listing_id}", response_class=HTMLResponse)
async def listing_detail(
    request: Request,
    listing_id: str,
    session: AsyncSession = Depends(get_session),
):
    try:
        uid = _uuid.UUID(listing_id)
    except ValueError:
        return HTMLResponse("<h2>Объявление не найдено</h2>", status_code=404)
    stmt = select(Listing).options(selectinload(Listing.analysis)).where(Listing.id == uid)
    result = await session.execute(stmt)
    listing = result.scalar_one_or_none()

    if listing is None:
        return HTMLResponse("<h2>Объявление не найдено</h2>", status_code=404)

    duplicate_listings: list[Listing] = []
    if listing.is_duplicate and listing.canonical_id is not None:
        canon_stmt = select(Listing).options(selectinload(Listing.analysis)).where(Listing.id == listing.canonical_id)
        canon_result = await session.execute(canon_stmt)
        canon = canon_result.scalar_one_or_none()
        if canon:
            duplicate_listings.append(canon)
        siblings_stmt = (
            select(Listing)
            .options(selectinload(Listing.analysis))
            .where(Listing.canonical_id == listing.canonical_id, Listing.id != listing.id)
        )
        siblings_result = await session.execute(siblings_stmt)
        duplicate_listings.extend(siblings_result.scalars().all())
    else:
        dupes_stmt = select(Listing).options(selectinload(Listing.analysis)).where(Listing.canonical_id == listing.id)
        dupes_result = await session.execute(dupes_stmt)
        duplicate_listings.extend(dupes_result.scalars().all())

    ctx = {
        "listing": listing,
        "categories": CATEGORY_LABELS,
        "duplicate_listings": duplicate_listings,
    }

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request, "partials/listing_detail.html", ctx)
    return templates.TemplateResponse(request, "detail.html", ctx)
