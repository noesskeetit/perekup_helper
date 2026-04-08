"""Tests for app.services.stale_detection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.stale_detection import mark_stale_listings


class TestMarkStaleListings:
    async def test_marks_old_listings_stale(self):
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("id1",), ("id2",)]

        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_result
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.stale_detection.async_session_factory", return_value=mock_session):
            count = await mark_stale_listings(stale_days=7)

        assert count == 2
        mock_session.commit.assert_awaited_once()

    async def test_no_stale_listings(self):
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []

        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_result
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.stale_detection.async_session_factory", return_value=mock_session):
            count = await mark_stale_listings(stale_days=7)

        assert count == 0
        mock_session.commit.assert_not_awaited()
