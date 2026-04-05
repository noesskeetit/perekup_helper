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


@app.get("/api/model-info")
async def model_info():
    """Get current price model metadata."""
    from app.services.pricing import get_price_model

    return get_price_model().get_info()
