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
    from sqlalchemy.ext.asyncio import AsyncSession
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
            "brand": l.brand,
            "model": l.model,
            "year": l.year,
            "price": l.price,
            "market_price": l.market_price,
            "diff_pct": float(l.price_diff_pct) if l.price_diff_pct else 0,
            "city": l.city,
            "url": l.url,
            "source": l.source,
            "category": l.analysis.category if l.analysis else None,
            "score": l.analysis.score if l.analysis else None,
            "ai_summary": l.analysis.ai_summary if l.analysis else None,
        }
        for l in listings
    ]


@app.get("/api/model-info")
async def model_info():
    """Get current price model metadata."""
    from app.services.pricing import get_price_model

    return get_price_model().get_info()
