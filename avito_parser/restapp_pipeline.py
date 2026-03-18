"""Pipeline using REST-App.net API instead of direct scraping."""

import logging
from dataclasses import dataclass, field

from app.db.session import async_session_factory

from .analysis import analyze_and_save
from .db import upsert_listing
from .restapp_client import fetch_listings

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    new: int = field(default=0)
    updated: int = field(default=0)
    analyzed: int = field(default=0)

    @property
    def total(self):
        return self.new + self.updated


async def run_restapp_pipeline(last_minutes=30, limit=50, **filters):
    """Fetch listings from REST-App.net and save to DB with AI analysis."""
    result = PipelineResult()

    items = fetch_listings(last_minutes=last_minutes, limit=limit, **filters)
    if not items:
        logger.info("No new listings from REST-App.net")
        return result

    logger.info("Processing %d listings from REST-App.net", len(items))

    async with async_session_factory() as session:
        try:
            for card_data in items:
                listing, is_new = await upsert_listing(session, card_data)
                if is_new:
                    result.new += 1
                else:
                    result.updated += 1

                analysis = await analyze_and_save(session, listing)
                if analysis is not None:
                    result.analyzed += 1

            await session.commit()
            logger.info(
                "REST-App pipeline complete: new=%d, updated=%d, analyzed=%d",
                result.new,
                result.updated,
                result.analyzed,
            )
        except Exception:
            await session.rollback()
            raise

    return result
