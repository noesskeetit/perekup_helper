"""Stale listing detection.

Marks listings as inactive (is_duplicate=True) if they haven't been
seen during ingestion for more than `stale_days`.

How it works:
- During ingestion, updated_at is refreshed for every re-seen listing.
- Listings not seen for N days are likely sold or removed from the source.
- This service marks them as is_duplicate to exclude from scoring/deals.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app.db.session import async_session_factory

logger = logging.getLogger(__name__)


async def mark_stale_listings(stale_days: int = 7) -> int:
    """Mark listings not seen for stale_days as inactive.

    Returns number of listings marked stale.
    """
    cutoff = datetime.now(UTC) - timedelta(days=stale_days)

    async with async_session_factory() as session:
        result = await session.execute(
            text("""
                UPDATE listings
                SET is_duplicate = true
                WHERE is_duplicate = false
                  AND updated_at < :cutoff
                  AND created_at < :cutoff
                RETURNING id
            """),
            {"cutoff": cutoff},
        )
        stale_ids = result.fetchall()
        count = len(stale_ids)

        if count > 0:
            await session.commit()
            logger.info("Marked %d listings as stale (not seen for %d+ days)", count, stale_days)
        else:
            logger.info("No stale listings found (cutoff: %s)", cutoff.isoformat())

        return count
