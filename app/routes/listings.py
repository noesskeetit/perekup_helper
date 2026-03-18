import uuid as _uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_session
from app.models.listing import Listing, ListingAnalysis  # noqa: F401

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


@router.get("/", response_class=HTMLResponse)
async def listings_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
    brand: str | None = Query(None),
    model: str | None = Query(None, alias="car_model"),
    year_from: int | None = Query(None),
    year_to: int | None = Query(None),
    price_from: int | None = Query(None),
    price_to: int | None = Query(None),
    diff_from: float | None = Query(None),
    diff_to: float | None = Query(None),
    market_diff_pct_min: float | None = Query(None),
    category: str | None = Query(None),
    hide_duplicates: bool = Query(True),
    sort_by: str = Query("created_at"),
    sort_dir: str = Query("desc"),
    page: int = Query(1, ge=1),
):
    per_page = 25
    stmt = select(Listing).options(selectinload(Listing.analysis))

    if brand:
        stmt = stmt.where(Listing.brand.ilike(f"%{brand}%"))
    if model:
        stmt = stmt.where(Listing.model.ilike(f"%{model}%"))
    if year_from:
        stmt = stmt.where(Listing.year >= year_from)
    if year_to:
        stmt = stmt.where(Listing.year <= year_to)
    if price_from:
        stmt = stmt.where(Listing.price >= price_from)
    if price_to:
        stmt = stmt.where(Listing.price <= price_to)
    if diff_from is not None:
        stmt = stmt.where(Listing.price_diff_pct >= diff_from)
    if diff_to is not None:
        stmt = stmt.where(Listing.price_diff_pct <= diff_to)
    if market_diff_pct_min is not None:
        stmt = stmt.where(Listing.price_diff_pct <= -market_diff_pct_min)
    if hide_duplicates:
        stmt = stmt.where(Listing.is_duplicate.is_(False))

    needs_join = category or sort_by in ("category", "confidence")
    if needs_join:
        stmt = stmt.join(ListingAnalysis, isouter=True)
    if category:
        stmt = stmt.where(ListingAnalysis.category == category)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await session.execute(count_stmt)).scalar() or 0

    sort_col = SORT_COLUMNS.get(sort_by, Listing.created_at)
    order_fn = desc if sort_dir == "desc" else asc
    stmt = stmt.order_by(order_fn(sort_col))
    stmt = stmt.offset((page - 1) * per_page).limit(per_page)

    result = await session.execute(stmt)
    listings = result.scalars().all()

    total_pages = max(1, (total + per_page - 1) // per_page)

    # Получаем уникальные марки и категории для фильтров
    brands_result = await session.execute(select(Listing.brand).distinct().order_by(Listing.brand))
    brands = [r[0] for r in brands_result.all()]

    ctx = {
        "request": request,
        "listings": listings,
        "brands": brands,
        "categories": CATEGORY_LABELS,
        "filters": {
            "brand": brand or "",
            "car_model": model or "",
            "year_from": year_from or "",
            "year_to": year_to or "",
            "price_from": price_from or "",
            "price_to": price_to or "",
            "diff_from": diff_from if diff_from is not None else "",
            "diff_to": diff_to if diff_to is not None else "",
            "market_diff_pct_min": market_diff_pct_min if market_diff_pct_min is not None else "",
            "category": category or "",
            "hide_duplicates": hide_duplicates,
        },
        "sort_by": sort_by,
        "sort_dir": sort_dir,
        "page": page,
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

    # Gather related duplicate listings for the detail card
    duplicate_listings: list[Listing] = []
    if listing.is_duplicate and listing.canonical_id is not None:
        canon_stmt = (
            select(Listing)
            .options(selectinload(Listing.analysis))
            .where(Listing.id == listing.canonical_id)
        )
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
        dupes_stmt = (
            select(Listing)
            .options(selectinload(Listing.analysis))
            .where(Listing.canonical_id == listing.id)
        )
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
