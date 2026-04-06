"""Tests for app/scheduler.py — periodic parse scheduler."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.parsers.pipeline import PipelineResult


class TestStartStopScheduler:
    def setup_method(self):
        # Reset module-level singleton between tests
        import app.scheduler as sched_mod

        sched_mod._scheduler = None

    def teardown_method(self):
        import app.scheduler as sched_mod

        if sched_mod._scheduler is not None and sched_mod._scheduler.running:
            sched_mod._scheduler.shutdown(wait=False)
        sched_mod._scheduler = None

    @patch("app.scheduler.AsyncIOScheduler")
    def test_start_creates_and_starts_scheduler(self, mock_cls):
        mock_instance = MagicMock()
        mock_instance.running = False
        mock_cls.return_value = mock_instance

        from app.scheduler import start_scheduler

        result = start_scheduler()

        mock_instance.add_job.assert_called_once()
        mock_instance.start.assert_called_once()
        assert result is mock_instance

    @patch("app.scheduler.AsyncIOScheduler")
    def test_start_idempotent_when_already_running(self, mock_cls):
        mock_instance = MagicMock()
        mock_instance.running = True

        import app.scheduler as sched_mod

        sched_mod._scheduler = mock_instance

        from app.scheduler import start_scheduler

        result = start_scheduler()

        mock_cls.assert_not_called()
        assert result is mock_instance

    @patch("app.scheduler.AsyncIOScheduler")
    def test_stop_shuts_down_running_scheduler(self, mock_cls):
        mock_instance = MagicMock()
        mock_instance.running = True

        import app.scheduler as sched_mod

        sched_mod._scheduler = mock_instance

        from app.scheduler import stop_scheduler

        stop_scheduler()

        mock_instance.shutdown.assert_called_once_with(wait=False)
        assert sched_mod._scheduler is None

    @patch("app.scheduler.AsyncIOScheduler")
    def test_stop_noop_when_not_running(self, mock_cls):
        mock_instance = MagicMock()
        mock_instance.running = False

        import app.scheduler as sched_mod

        sched_mod._scheduler = mock_instance

        from app.scheduler import stop_scheduler

        stop_scheduler()

        mock_instance.shutdown.assert_not_called()

    @patch("app.scheduler.AsyncIOScheduler")
    def test_job_uses_configured_interval(self, mock_cls):
        mock_instance = MagicMock()
        mock_instance.running = False
        mock_cls.return_value = mock_instance

        from app.config import settings
        from app.scheduler import start_scheduler

        start_scheduler()

        call_kwargs = mock_instance.add_job.call_args
        assert call_kwargs.kwargs.get("minutes") == settings.parse_interval_minutes or (
            len(call_kwargs.args) >= 3 and call_kwargs.args[2] == settings.parse_interval_minutes
        )


class TestParseJob:
    @pytest.mark.asyncio
    async def test_parse_job_logs_result(self):
        fake_result = PipelineResult(total_new=2, total_scored=1, total_analyzed=3)

        with patch("app.scheduler.run_pipeline", new_callable=AsyncMock, return_value=fake_result):
            from app.scheduler import _parse_job

            # Should not raise
            await _parse_job()

    @pytest.mark.asyncio
    async def test_parse_job_handles_exception(self):
        with patch("app.scheduler.run_pipeline", new_callable=AsyncMock, side_effect=RuntimeError("DB down")):
            from app.scheduler import _parse_job

            # Should swallow exception without re-raising
            await _parse_job()
