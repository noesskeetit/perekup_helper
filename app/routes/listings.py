import uuid as _uuid
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
}


async def _get_stats(session: AsyncSession) -> dict:
    """Compute dashboard summary stats."""
    total = (await session.execute(select(func.count()).select_from(Listing).where(Listing.is_duplicate.is_(False)))).scalar() or 0

    below_market = 0
    hot_deals = 0
    avg_diff = None

    if total > 0:
        below_market = (
            await session.execute(
                select(func.count())
                .select_from(Listing)
                .where(Listing.is_duplicate.is_(False), Listing.price_diff_pct > 0)
            )
        ).scalar() or 0

        hot_deals = (
            await session.execute(
                select(func.count())
                .select_from(Listing)
                .where(Listing.is_duplicate.is_(False), Listing.price_diff_pct > 15)
            )
        ).scalar() or 0

        avg_diff_val = (
            await session.execute(
                select(func.avg(Listing.price_diff_pct))
                .where(Listing.is_duplicate.is_(False), Listing.price_diff_pct.isnot(None))
            )
        ).scalar()
        avg_diff = float(avg_diff_val) if avg_diff_val is not None else None

    # Price model info
    model_info = None
    try:
        from app.services.pricing import get_price_model

        pm = get_price_model()
        if pm.is_trained:
            model_info = pm.get_info()
    except Exception:
        pass

    return {
        "total": total,
        "below_market": below_market,
        "hot_deals": hot_deals,
        "avg_diff": avg_diff,
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
