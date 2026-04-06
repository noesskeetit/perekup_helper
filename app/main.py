from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db.session import engine
from app.models.base import Base
from app.routes.listings import router as listings_router
from app.routes.stats import router as stats_router
from app.scheduler import start_scheduler, stop_scheduler

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
    return {"status": "ok"}


@app.post("/api/run-pipeline")
async def run_pipeline_now():
    """Manually trigger the full pipeline (parse + score + analyze)."""
    from app.parsers.pipeline import run_pipeline

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


@app.post("/api/retrain-model")
async def retrain_model_now():
    """Manually retrain the CatBoost price model."""
    from app.services.pricing_trainer import score_listings, train_model

    stats = await train_model()
    scored = 0
    if stats.get("status") == "trained":
        scored = await score_listings(limit=5000)
    return {"training": stats, "scored": scored}


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

    return [
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
                    (Listing.is_duplicate.is_(False)) & (Listing.price_diff_pct > 15),
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


@app.get("/api/model-info")
async def model_info():
    """Get current price model metadata."""
    from app.services.pricing import get_price_model

    return get_price_model().get_info()
