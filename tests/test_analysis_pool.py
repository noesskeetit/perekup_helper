"""Tests for app.services.analysis_pool."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.analysis_pool import (
    PoolMetrics,
    _pick_worker_count,
    run_analysis_pool,
)


class TestPickWorkerCount:
    def test_zero_backlog(self):
        assert _pick_worker_count(0) == 1

    def test_small_backlog(self):
        assert _pick_worker_count(10) == 1
        assert _pick_worker_count(49) == 1

    def test_medium_backlog(self):
        assert _pick_worker_count(50) == 2
        assert _pick_worker_count(200) == 2
        assert _pick_worker_count(499) == 2

    def test_large_backlog(self):
        assert _pick_worker_count(500) == 3
        assert _pick_worker_count(1000) == 3
        assert _pick_worker_count(10000) == 3


class TestPoolMetrics:
    def test_default_values(self):
        m = PoolMetrics()
        assert m.backlog == 0
        assert m.workers == 0
        assert m.processed == 0
        assert m.failed == 0
        assert m.errors == []
        assert m.details == []

    def test_to_dict(self):
        m = PoolMetrics(backlog=100, workers=2, processed=50, failed=1, errors=["err1"])
        d = m.to_dict()
        assert d["backlog"] == 100
        assert d["workers"] == 2
        assert d["processed"] == 50
        assert d["failed"] == 1
        assert d["errors"] == ["err1"]
        assert "details" in d


class TestRunAnalysisPool:
    @pytest.mark.asyncio
    async def test_empty_backlog(self):
        with patch("app.services.analysis_pool.get_backlog_size", new_callable=AsyncMock, return_value=0):
            result = await run_analysis_pool()

        assert result["backlog"] == 0
        assert result["processed"] == 0
        assert result["failed"] == 0
        assert result["errors"] == []

    @pytest.mark.asyncio
    async def test_single_worker_success(self):
        with (
            patch("app.services.analysis_pool.get_backlog_size", new_callable=AsyncMock, return_value=10),
            patch("app.services.analysis_pool._run_worker", new_callable=AsyncMock, return_value=10),
        ):
            result = await run_analysis_pool()

        assert result["backlog"] == 10
        assert result["workers"] == 1
        assert result["processed"] == 10
        assert result["failed"] == 0
        assert result["errors"] == []
        assert len(result["details"]) == 1
        assert result["details"][0]["name"] == "mistral-nemo"
        assert result["details"][0]["processed"] == 10

    @pytest.mark.asyncio
    async def test_worker_failure(self):
        async def _mock_worker(name, provider, model, batch_size, limit):
            raise RuntimeError("API down")

        with (
            patch("app.services.analysis_pool.get_backlog_size", new_callable=AsyncMock, return_value=10),
            patch("app.services.analysis_pool._run_worker", side_effect=_mock_worker),
        ):
            result = await run_analysis_pool()

        assert result["workers"] == 1
        assert result["processed"] == 0
        assert result["failed"] == 1
        assert len(result["errors"]) == 1
        assert "API down" in result["errors"][0]

    @pytest.mark.asyncio
    async def test_multi_worker_mixed(self):
        call_count = 0

        async def _mock_worker(name, provider, model, batch_size, limit):
            nonlocal call_count
            call_count += 1
            if provider == "openrouter" and "qwen" in model:
                raise RuntimeError("qwen failed")
            return 25

        with (
            patch("app.services.analysis_pool.get_backlog_size", new_callable=AsyncMock, return_value=100),
            patch("app.services.analysis_pool._run_worker", side_effect=_mock_worker),
        ):
            result = await run_analysis_pool()

        assert result["workers"] == 2
        assert result["processed"] == 25
        assert result["failed"] == 1
        assert len(result["errors"]) == 1

    @pytest.mark.asyncio
    async def test_three_workers_at_500(self):
        with (
            patch("app.services.analysis_pool.get_backlog_size", new_callable=AsyncMock, return_value=600),
            patch("app.services.analysis_pool._run_worker", new_callable=AsyncMock, return_value=100),
        ):
            result = await run_analysis_pool()

        assert result["workers"] == 3
        assert result["processed"] == 300
        assert len(result["details"]) == 3

    @pytest.mark.asyncio
    async def test_metrics_keys_present(self):
        with patch("app.services.analysis_pool.get_backlog_size", new_callable=AsyncMock, return_value=0):
            result = await run_analysis_pool()

        required_keys = {"backlog", "workers", "processed", "failed", "errors", "details"}
        assert required_keys == set(result.keys())
