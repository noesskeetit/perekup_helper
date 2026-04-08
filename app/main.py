from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import engine, get_session
from app.models.base import Base
from app.routes.listings import router as listings_router
from app.routes.stats import router as stats_router
from app.scheduler import start_scheduler, stop_scheduler


def _price_drop_info(listing) -> tuple[bool, float]:
    """Return (price_dropped, price_drop_pct) from the most recent price_history entry.

    price_drop_pct is positive when the price went down (e.g. 12.5 means -12.5%).
    Returns (False, 0.0) when there is no history or the last change was not a drop.
    """
    raw = listing.raw_data if listing.raw_data else {}
    history: list[dict] = raw.get("price_history", [])
    if not history:
        return False, 0.0
    last_price = history[-1].get("price")
    if last_price is None or last_price <= 0:
        return False, 0.0
    if listing.price >= last_price:
        return False, 0.0
    drop_pct = round((last_price - listing.price) / last_price * 100, 2)
    return True, drop_pct


BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="PerekupHelper", version="0.2.0", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.include_router(listings_router)
app.include_router(stats_router)


@app.get("/health")
async def health():
    """Health check with DB connectivity and model status."""
    from app.db.session import async_session_factory

    checks = {"status": "ok", "db": "unknown", "model": "unknown"}
    try:
        async with async_session_factory() as session:
            from sqlalchemy import text

            await session.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception:
        checks["db"] = "error"
        checks["status"] = "degraded"

    try:
        from app.services.pricing import get_price_model

        model = get_price_model()
        checks["model"] = "trained" if model.is_trained else "not_trained"
    except Exception:
        checks["model"] = "error"

    return checks


@app.post("/api/run-pipeline")
async def run_pipeline_now():
    """Manually trigger the full pipeline (parse + score + analyze)."""
    import logging
    import traceback

    from app.parsers.pipeline import run_pipeline

    logger = logging.getLogger(__name__)
    try:
        result = await run_pipeline()
        return {
            "total_new": result.total_new,
            "total_scored": result.total_scored,
            "total_analyzed": result.total_analyzed,
            "errors": result.errors,
            "sources": [
                {"source": r.source, "fetched": r.total_fetched, "new": r.new_saved, "dupes": r.duplicates_skipped}
                for r in result.source_results
            ],
        }
    except Exception:
        logger.exception("Pipeline crashed with unhandled exception")
        return {
            "error": traceback.format_exc(),
            "total_new": 0,
            "total_scored": 0,
            "total_analyzed": 0,
            "errors": ["pipeline_crash"],
        }


@app.post("/api/retrain-model")
async def retrain_model_now():
    """Manually retrain the CatBoost price model."""
    from app.services.pricing_trainer import score_listings, train_model

    # Exclude Auto.ru from training due to price data quality issues
    stats = await train_model(exclude_sources=["autoru"])
    scored = 0
    if stats.get("status") == "trained":
        scored = await score_listings(limit=5000)

    # Also run deal scoring
    from app.services.deal_scorer import score_deals

    deal_scored = await score_deals(limit=5000)

    return {"training": stats, "price_scored": scored, "deal_scored": deal_scored}


@app.post("/api/run-analysis")
async def run_analysis_now(max_total: int = 2000):
    """Run auto-scaling AI analysis pool on backlog."""
    from app.services.analysis_pool import run_analysis_pool

    return await run_analysis_pool(max_total=max_total)


@app.get("/api/hot-deals")
async def hot_deals(
    min_diff: float = 15.0,
    category: str | None = "clean",
    limit: int = 50,
    city: str | None = None,
):
    """Get hot deals — listings significantly below market price.

    Ready-made JSON for Telegram bot or external consumers.
    """
    from sqlalchemy import desc, select
    from sqlalchemy.orm import selectinload

    from app.db.session import async_session_factory
    from app.models.listing import Listing, ListingAnalysis

    async with async_session_factory() as session:
        stmt = (
            select(Listing)
            .options(selectinload(Listing.analysis))
            .where(
                Listing.is_duplicate.is_(False),
                Listing.price_diff_pct >= min_diff,
                Listing.market_price.isnot(None),
            )
        )
        if category:
            stmt = stmt.join(ListingAnalysis).where(ListingAnalysis.category == category)
        if city:
            stmt = stmt.where(Listing.city.ilike(f"%{city}%"))
        stmt = stmt.order_by(desc(Listing.price_diff_pct)).limit(limit)

        result = await session.execute(stmt)
        listings = result.scalars().all()

    results = []
    for listing in listings:
        dropped, drop_pct = _price_drop_info(listing)
        results.append(
            {
                "brand": listing.brand,
                "model": listing.model,
                "year": listing.year,
                "price": listing.price,
                "market_price": listing.market_price,
                "diff_pct": float(listing.price_diff_pct) if listing.price_diff_pct else 0,
                "city": listing.city,
                "url": listing.url,
                "source": listing.source,
                "category": listing.analysis.category if listing.analysis else None,
                "score": listing.analysis.score if listing.analysis else None,
                "ai_summary": listing.analysis.ai_summary if listing.analysis else None,
                "price_dropped": dropped,
                "price_drop_pct": drop_pct,
            }
        )
    return results


@app.get("/api/export-csv")
async def export_csv(
    min_diff: float = 10.0,
    limit: int = 500,
    session: AsyncSession = Depends(get_session),
):
    """Export hot deals as CSV for spreadsheet analysis."""
    import csv
    import io

    from fastapi.responses import StreamingResponse
    from sqlalchemy import select

    from app.models.listing import Listing

    stmt = (
        select(Listing)
        .where(
            Listing.is_duplicate.is_(False),
            Listing.price > 0,
            Listing.price_diff_pct >= min_diff,
        )
        .order_by(Listing.price_diff_pct.desc())
        .limit(min(limit, 1000))
    )
    result = await session.execute(stmt)
    listings = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["brand", "model", "year", "price", "market_price", "diff_%", "deal_score", "mileage", "city", "source", "url"]
    )
    for row in listings:
        writer.writerow(
            [
                row.brand,
                row.model,
                row.year,
                row.price,
                row.market_price,
                float(row.price_diff_pct) if row.price_diff_pct else "",
                row.deal_score or "",
                row.mileage or "",
                row.city or "",
                row.source,
                row.url,
            ]
        )

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=hot_deals.csv"},
    )


@app.get("/api/price-drops")
async def price_drops(
    min_drop_pct: float = 1.0,
    limit: int = 50,
    city: str | None = None,
):
    """Listings with recent price drops, sorted by drop percentage (descending).

    Only considers listings whose raw_data contains a price_history
    and whose last price change was a decrease.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.db.session import async_session_factory
    from app.models.listing import Listing

    async with async_session_factory() as session:
        stmt = (
            select(Listing)
            .options(selectinload(Listing.analysis))
            .where(
                Listing.is_duplicate.is_(False),
                Listing.raw_data.isnot(None),
            )
        )
        if city:
            stmt = stmt.where(Listing.city.ilike(f"%{city}%"))
        # Fetch a generous batch; filtering by JSON content happens in Python.
        stmt = stmt.order_by(Listing.updated_at.desc()).limit(limit * 10)

        result = await session.execute(stmt)
        listings = result.scalars().all()

    items = []
    for listing in listings:
        dropped, drop_pct = _price_drop_info(listing)
        if not dropped or drop_pct < min_drop_pct:
            continue
        items.append(
            {
                "brand": listing.brand,
                "model": listing.model,
                "year": listing.year,
                "price": listing.price,
                "market_price": listing.market_price,
                "diff_pct": float(listing.price_diff_pct) if listing.price_diff_pct else 0,
                "city": listing.city,
                "url": listing.url,
                "source": listing.source,
                "category": listing.analysis.category if listing.analysis else None,
                "price_dropped": True,
                "price_drop_pct": drop_pct,
            }
        )
    items.sort(key=lambda d: d["price_drop_pct"], reverse=True)
    return items[:limit]


@app.get("/api/top-deals")
async def top_deals(limit: int = 20, min_score: int = 70):
    """Top deals ranked by deal_score (0-100).

    Uses the pre-computed deal_score which factors in:
    price vs market, AI category, mileage, photos, freshness, owners, data completeness.
    """
    from sqlalchemy import desc, select
    from sqlalchemy.orm import selectinload

    from app.db.session import async_session_factory
    from app.models.listing import Listing

    async with async_session_factory() as session:
        stmt = (
            select(Listing)
            .options(selectinload(Listing.analysis))
            .where(
                Listing.is_duplicate.is_(False),
                Listing.deal_score.isnot(None),
                Listing.deal_score >= min_score,
                Listing.price > 100_000,  # filter garbage prices
            )
            .order_by(desc(Listing.deal_score), desc(Listing.price_diff_pct))
            .limit(limit)
        )
        result = await session.execute(stmt)
        listings = list(result.scalars().all())

    return [
        {
            "brand": listing.brand,
            "model": listing.model,
            "year": listing.year,
            "price": listing.price,
            "market_price": listing.market_price,
            "diff_pct": float(listing.price_diff_pct) if listing.price_diff_pct else 0,
            "deal_score": int(listing.deal_score),
            "city": listing.city,
            "mileage": listing.mileage,
            "url": listing.url,
            "source": listing.source,
            "category": listing.analysis.category if listing.analysis else None,
            "ai_summary": listing.analysis.ai_summary if listing.analysis else None,
        }
        for listing in listings
    ]


@app.get("/api/stats")
async def api_stats():
    """All key metrics in one JSON call — for bots and programmatic access."""
    from sqlalchemy import case, func, select

    from app.db.session import async_session_factory
    from app.models.listing import Listing, ListingAnalysis
    from app.services.pricing import get_price_model

    async with async_session_factory() as session:
        # ── Single aggregate query over listings ──
        total_col = func.count(Listing.id)
        unique_col = func.count(case((Listing.is_duplicate.is_(False), Listing.id)))
        dupes_col = func.count(case((Listing.is_duplicate.is_(True), Listing.id)))
        hot_col = func.count(
            case(
                (
                    (Listing.is_duplicate.is_(False)) & (Listing.deal_score >= 70),
                    Listing.id,
                )
            )
        )
        avg_price_col = func.avg(Listing.price)
        avg_discount_col = func.avg(
            case(
                (Listing.price_diff_pct.isnot(None), Listing.price_diff_pct),
            )
        )

        agg_stmt = select(
            total_col,
            unique_col,
            dupes_col,
            hot_col,
            avg_price_col,
            avg_discount_col,
        )
        agg_row = (await session.execute(agg_stmt)).one()
        total_listings = agg_row[0] or 0
        unique_listings = agg_row[1] or 0
        duplicates = agg_row[2] or 0
        hot_deals_count = agg_row[3] or 0
        avg_price = round(float(agg_row[4]), 0) if agg_row[4] else 0
        avg_discount = round(float(agg_row[5]), 2) if agg_row[5] else 0

        # ── By-source breakdown ──
        src_stmt = select(Listing.source, func.count(Listing.id)).group_by(Listing.source)
        src_rows = (await session.execute(src_stmt)).all()
        by_source = {row[0]: row[1] for row in src_rows}

        # ── Analyzed count ──
        analyzed_stmt = select(func.count(ListingAnalysis.id))
        analyzed_count = (await session.execute(analyzed_stmt)).scalar() or 0

    analyzed_pct = round(analyzed_count / unique_listings * 100, 1) if unique_listings else 0

    return {
        "total_listings": total_listings,
        "unique_listings": unique_listings,
        "duplicates": duplicates,
        "by_source": by_source,
        "analyzed_count": analyzed_count,
        "analyzed_pct": analyzed_pct,
        "hot_deals_count": hot_deals_count,
        "avg_price": avg_price,
        "avg_discount": avg_discount,
        "model_info": get_price_model().get_info(),
    }


@app.get("/api/price-calculator")
async def price_calculator(
    brand: str,
    model: str,
    year: int,
    mileage: int = 0,
    engine_volume: float = 0.0,
    transmission: str = "unknown",
    city: str = "unknown",
):
    """Estimate market price for any car by parameters (no listing_id needed)."""
    from app.services.pricing import get_price_model

    pm = get_price_model()
    if not pm.is_trained:
        return {"error": "model not trained"}

    result = pm.predict_one(
        {
            "brand": brand,
            "model": model,
            "year": year,
            "mileage": mileage,
            "price": 0,
            "source": "manual",
            "city": city,
            "engine_type": "unknown",
            "transmission": transmission,
            "drive_type": "unknown",
            "body_type": "unknown",
            "engine_volume": engine_volume,
            "power_hp": 0,
            "owners_count": 0,
            "photo_count": 0,
            "is_dealer": 0,
            "listing_date": None,
            "created_at": None,
            "description": "",
        }
    )
    return {
        "brand": brand,
        "model": model,
        "year": year,
        "mileage": mileage,
        "estimated_price": result.get("p50"),
        "price_range": {"low": result.get("p10"), "high": result.get("p90")},
    }


@app.get("/api/price-estimate/{listing_id}")
async def price_estimate(listing_id: str):
    """Ensemble price estimate: CatBoost + comparable sales + Avito estimate.

    Weights (all three available):  avito=0.5, comparable=0.3, catboost=0.2
    Avito missing:                  comparable=0.5, catboost=0.5
    Comparables < 3:                catboost=0.7, comparable=0.3
    """
    import uuid

    from sqlalchemy import select

    from app.db.session import async_session_factory
    from app.models.listing import Listing
    from app.services.comparable_sales import compute_comparable_price, find_comparables
    from app.services.pricing import get_price_model

    # 1. Fetch listing from DB
    try:
        lid = uuid.UUID(listing_id)
    except ValueError:
        return {"error": "invalid listing_id"}, 400

    async with async_session_factory() as session:
        stmt = select(Listing).where(Listing.id == lid)
        result = await session.execute(stmt)
        listing = result.scalar_one_or_none()

    if listing is None:
        return {"error": "listing not found"}

    # 2. CatBoost prediction
    model = get_price_model()
    cb_input = {
        "brand": listing.brand,
        "model": listing.model,
        "year": listing.year,
        "mileage": listing.mileage or 0,
        "price": listing.price,
        "source": listing.source or "unknown",
        "city": listing.city or "unknown",
        "engine_type": listing.engine_type or "unknown",
        "engine_volume": listing.engine_volume or 0.0,
        "power_hp": listing.power_hp or 0,
        "transmission": listing.transmission or "unknown",
        "drive_type": listing.drive_type or "unknown",
        "body_type": listing.body_type or "unknown",
        "owners_count": listing.owners_count or 0,
    }
    cb_pred = model.predict_one(cb_input)
    catboost_price = cb_pred["p50"]

    # 3. Comparable sales (K=10, 60 days)
    comps = await find_comparables(cb_input, k=10, max_age_days=60)
    comp_stats = compute_comparable_price(comps)
    comparable_price = comp_stats["median_price"]
    comp_count = comp_stats["count"]

    # 4. Avito estimate from raw_data
    avito_estimate = None
    if listing.raw_data and isinstance(listing.raw_data, dict):
        avito_estimate = listing.raw_data.get("avito_estimate")

    # 5. Ensemble price
    ensemble_price = None
    method = "none"

    has_catboost = catboost_price is not None and model.is_trained
    has_comparable = comparable_price is not None and comp_count >= 1
    has_avito = avito_estimate is not None

    if has_avito and has_comparable and comp_count >= 3 and has_catboost:
        # All three available with enough comparables
        ensemble_price = int(avito_estimate * 0.5 + comparable_price * 0.3 + catboost_price * 0.2)
        method = "avito+comparable+catboost"
    elif has_avito and has_catboost and (not has_comparable or comp_count < 3):
        # Avito + catboost, weak/no comparables
        ensemble_price = int(avito_estimate * 0.5 + catboost_price * 0.5)
        method = "avito+catboost"
    elif has_avito and has_comparable and comp_count >= 3:
        # Avito + comparables, no catboost
        ensemble_price = int(avito_estimate * 0.5 + comparable_price * 0.5)
        method = "avito+comparable"
    elif has_comparable and comp_count >= 3 and has_catboost:
        # No avito, enough comparables
        ensemble_price = int(comparable_price * 0.5 + catboost_price * 0.5)
        method = "comparable+catboost"
    elif has_comparable and comp_count < 3 and has_catboost:
        # Few comparables, lean on catboost
        ensemble_price = int(catboost_price * 0.7 + comparable_price * 0.3)
        method = "catboost_heavy+comparable"
    elif has_catboost:
        ensemble_price = catboost_price
        method = "catboost_only"
    elif has_comparable:
        ensemble_price = comparable_price
        method = "comparable_only"
    elif has_avito:
        ensemble_price = avito_estimate
        method = "avito_only"

    # 6. Confidence score (0.0 - 1.0)
    confidence = 0.0
    if has_avito:
        confidence += 0.4
    if has_catboost:
        confidence += 0.2
    if comp_count >= 10:
        confidence += 0.4
    elif comp_count >= 3:
        confidence += 0.2 + 0.2 * (comp_count - 3) / 7
    elif comp_count >= 1:
        confidence += 0.1
    confidence = round(min(confidence, 1.0), 2)

    return {
        "listing_id": str(listing.id),
        "actual_price": listing.price,
        "estimates": {
            "catboost": cb_pred if has_catboost else None,
            "comparable": {
                "median_price": comparable_price,
                "p25_price": comp_stats["p25_price"],
                "p75_price": comp_stats["p75_price"],
                "count": comp_count,
            }
            if has_comparable
            else None,
            "avito": avito_estimate,
        },
        "ensemble": {
            "price": ensemble_price,
            "method": method,
            "confidence": confidence,
        },
        "comparables_count": comp_count,
    }


@app.get("/api/search")
async def search_listings(
    brand: str | None = None,
    model: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    price_from: int | None = None,
    price_to: int | None = None,
    min_discount: float | None = None,
    source: str | None = None,
    limit: int = 20,
    session: AsyncSession = Depends(get_session),
):
    """Search listings with filters. Returns top results by deal_score."""
    from sqlalchemy import func, select

    from app.models.listing import Listing

    stmt = select(Listing).where(Listing.is_duplicate.is_(False), Listing.price > 0)

    if brand:
        stmt = stmt.where(func.lower(Listing.brand) == brand.lower())
    if model:
        stmt = stmt.where(func.lower(Listing.model) == model.lower())
    if year_from:
        stmt = stmt.where(Listing.year >= year_from)
    if year_to:
        stmt = stmt.where(Listing.year <= year_to)
    if price_from:
        stmt = stmt.where(Listing.price >= price_from)
    if price_to:
        stmt = stmt.where(Listing.price <= price_to)
    if min_discount:
        stmt = stmt.where(Listing.price_diff_pct >= min_discount)
    if source:
        stmt = stmt.where(Listing.source == source.lower())

    stmt = stmt.order_by(Listing.deal_score.desc().nullslast(), Listing.created_at.desc()).limit(min(limit, 50))

    result = await session.execute(stmt)
    listings = result.scalars().all()

    return [
        {
            "brand": listing.brand,
            "model": listing.model,
            "year": listing.year,
            "price": listing.price,
            "market_price": listing.market_price,
            "diff_pct": float(listing.price_diff_pct) if listing.price_diff_pct else None,
            "deal_score": listing.deal_score,
            "mileage": listing.mileage,
            "city": listing.city,
            "source": listing.source,
            "url": listing.url,
        }
        for listing in listings
    ]


@app.get("/api/brands")
async def list_brands(session: AsyncSession = Depends(get_session)):
    """List all brands with listing counts, sorted by count."""
    from sqlalchemy import func, select

    from app.models.listing import Listing

    stmt = (
        select(Listing.brand, func.count(Listing.id).label("count"))
        .where(Listing.is_duplicate.is_(False))
        .group_by(Listing.brand)
        .order_by(func.count(Listing.id).desc())
    )
    result = await session.execute(stmt)
    return [{"brand": row[0], "count": row[1]} for row in result.all()]


@app.get("/api/models/{brand}")
async def list_models(brand: str, session: AsyncSession = Depends(get_session)):
    """List all models for a brand with listing counts."""
    from sqlalchemy import func, select

    from app.models.listing import Listing

    stmt = (
        select(Listing.model, func.count(Listing.id).label("count"))
        .where(Listing.is_duplicate.is_(False), func.lower(Listing.brand) == brand.lower())
        .group_by(Listing.model)
        .order_by(func.count(Listing.id).desc())
    )
    result = await session.execute(stmt)
    return [{"model": row[0], "count": row[1]} for row in result.all()]


@app.get("/api/model-info")
async def model_info():
    """Get current price model metadata."""
    from app.services.pricing import get_price_model

    return get_price_model().get_info()


@app.get("/api/dashboard")
async def dashboard():
    """All-in-one dashboard payload for Telegram bot or mobile app.

    Returns stats, top deals, recent price drops, fresh listings,
    and model health in a single DB session.
    """
    from datetime import UTC, datetime

    from sqlalchemy import case, desc, func, select
    from sqlalchemy.orm import selectinload

    from app.db.session import async_session_factory
    from app.models.listing import Listing, ListingAnalysis
    from app.services.pricing import get_price_model

    now = datetime.now(UTC)

    async with async_session_factory() as session:
        # ── 1. Aggregate stats ──
        total_col = func.count(Listing.id)
        unique_col = func.count(case((Listing.is_duplicate.is_(False), Listing.id)))
        hot_col = func.count(
            case(
                (
                    (Listing.is_duplicate.is_(False)) & (Listing.deal_score >= 70),
                    Listing.id,
                )
            )
        )

        agg_stmt = select(total_col, unique_col, hot_col)
        agg_row = (await session.execute(agg_stmt)).one()
        total_listings = agg_row[0] or 0
        unique_listings = agg_row[1] or 0
        hot_deals_count = agg_row[2] or 0

        # By-source breakdown
        src_stmt = select(Listing.source, func.count(Listing.id)).group_by(Listing.source)
        src_rows = (await session.execute(src_stmt)).all()
        by_source = {row[0]: row[1] for row in src_rows}

        # Analyzed count
        analyzed_stmt = select(func.count(ListingAnalysis.id))
        analyzed_count = (await session.execute(analyzed_stmt)).scalar() or 0

        # ── 2. Top 5 deals by deal_score ──
        top_stmt = (
            select(Listing)
            .options(selectinload(Listing.analysis))
            .where(
                Listing.is_duplicate.is_(False),
                Listing.deal_score.isnot(None),
                Listing.deal_score >= 70,
                Listing.price > 100_000,
            )
            .order_by(desc(Listing.deal_score), desc(Listing.price_diff_pct))
            .limit(5)
        )
        top_result = await session.execute(top_stmt)
        top_listings = list(top_result.scalars().all())

        top_deals = [
            {
                "brand": listing.brand,
                "model": listing.model,
                "year": listing.year,
                "price": listing.price,
                "market_price": listing.market_price,
                "diff_pct": float(listing.price_diff_pct) if listing.price_diff_pct else 0,
                "deal_score": int(listing.deal_score),
                "url": listing.url,
            }
            for listing in top_listings
        ]

        # ── 3. Recent price drops (last 5) ──
        drops_stmt = (
            select(Listing)
            .options(selectinload(Listing.analysis))
            .where(
                Listing.is_duplicate.is_(False),
                Listing.raw_data.isnot(None),
            )
            .order_by(Listing.updated_at.desc())
            .limit(200)
        )
        drops_result = await session.execute(drops_stmt)
        drop_candidates = drops_result.scalars().all()

        recent_price_drops = []
        for listing in drop_candidates:
            dropped, drop_pct = _price_drop_info(listing)
            if not dropped:
                continue
            history = (listing.raw_data or {}).get("price_history", [])
            old_price = history[-1].get("price") if history else None
            recent_price_drops.append(
                {
                    "brand": listing.brand,
                    "model": listing.model,
                    "price": listing.price,
                    "old_price": old_price,
                    "drop_pct": drop_pct,
                    "url": listing.url,
                }
            )
            if len(recent_price_drops) >= 5:
                break

        # ── 4. Fresh listings (5 newest) ──
        fresh_stmt = select(Listing).where(Listing.is_duplicate.is_(False)).order_by(Listing.created_at.desc()).limit(5)
        fresh_result = await session.execute(fresh_stmt)
        fresh_rows = fresh_result.scalars().all()

        fresh_listings = []
        for listing in fresh_rows:
            age_minutes = (now - listing.created_at).total_seconds() / 60 if listing.created_at else None
            fresh_listings.append(
                {
                    "brand": listing.brand,
                    "model": listing.model,
                    "year": listing.year,
                    "price": listing.price,
                    "source": listing.source,
                    "age_minutes": round(age_minutes, 1) if age_minutes is not None else None,
                }
            )

    # ── 5. Model health (outside session — no DB needed) ──
    model = get_price_model()
    info = model.get_info()
    model_health = {
        "is_trained": info["is_trained"],
        "samples": info["training_size"],
        "p50_mape": None,
    }

    return {
        "stats": {
            "total": total_listings,
            "unique": unique_listings,
            "analyzed": analyzed_count,
            "hot_deals": hot_deals_count,
            "by_source": by_source,
        },
        "top_deals": top_deals[:5],
        "recent_price_drops": recent_price_drops,
        "fresh_listings": fresh_listings,
        "model_health": model_health,
    }
