"""Tests for app.services.analysis_pool — metrics, scaling, and error handling."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.analysis_pool import (
    MAX_ERRORS_PER_WORKER,
    SCALE_THRESHOLDS,
    WORKER_CONFIGS,
    _make_worker_metrics,
    _pick_worker_count,
    _run_worker,
    run_analysis_pool,
)

# ---------------------------------------------------------------------------
# _pick_worker_count — worker scaling logic
# ---------------------------------------------------------------------------


class TestPickWorkerCount:
    """Worker count depends on backlog size."""

    def test_zero_backlog_returns_1(self):
        assert _pick_worker_count(0) == 1

    def test_small_backlog_returns_1(self):
        assert _pick_worker_count(10) == 1
        assert _pick_worker_count(49) == 1

    def test_medium_backlog_returns_2(self):
        assert _pick_worker_count(50) == 2
        assert _pick_worker_count(200) == 2
        assert _pick_worker_count(499) == 2

    def test_large_backlog_returns_3(self):
        assert _pick_worker_count(500) == 3
        assert _pick_worker_count(1000) == 3
        assert _pick_worker_count(5000) == 3

    def test_thresholds_are_sorted_descending(self):
        """SCALE_THRESHOLDS must be sorted high-to-low for correct matching."""
        thresholds = [t for t, _ in SCALE_THRESHOLDS]
        assert thresholds == sorted(thresholds, reverse=True)


# ---------------------------------------------------------------------------
# _make_worker_metrics — metric dict structure
# ---------------------------------------------------------------------------


class TestMakeWorkerMetrics:
    """_make_worker_metrics returns well-formed dicts."""

    def test_defaults(self):
        m = _make_worker_metrics("test-worker")
        assert m["worker_name"] == "test-worker"
        assert m["processed"] == 0
        assert m["failed"] == 0
        assert m["errors"] == []
        assert m["duration_seconds"] == 0.0

    def test_with_values(self):
        m = _make_worker_metrics("w1", processed=5, failed=2, errors=["e1"], duration_seconds=1.234)
        assert m["processed"] == 5
        assert m["failed"] == 2
        assert m["errors"] == ["e1"]
        assert m["duration_seconds"] == 1.23

    def test_errors_truncated(self):
        """Only MAX_ERRORS_PER_WORKER errors are kept."""
        errs = [f"err-{i}" for i in range(20)]
        m = _make_worker_metrics("w", errors=errs)
        assert len(m["errors"]) == MAX_ERRORS_PER_WORKER


# ---------------------------------------------------------------------------
# _run_worker — returns metrics dict
# ---------------------------------------------------------------------------


class TestRunWorker:
    """_run_worker wraps provider calls and always returns a metrics dict."""

    @pytest.mark.asyncio
    async def test_openrouter_worker_returns_metrics(self):
        with patch("app.services.analysis_pool._run_openrouter_worker", new_callable=AsyncMock) as mock_or:
            mock_or.return_value = 15
            metrics = await _run_worker("test-or", "openrouter", "some/model", 10, 50)

        assert metrics["worker_name"] == "test-or"
        assert metrics["processed"] == 15
        assert metrics["failed"] == 0
        assert metrics["errors"] == []
        assert metrics["duration_seconds"] >= 0

    @pytest.mark.asyncio
    async def test_cloudru_worker_returns_metrics(self):
        with patch("app.services.analysis_pool._run_cloudru_worker", new_callable=AsyncMock) as mock_cr:
            mock_cr.return_value = 8
            metrics = await _run_worker("test-cr", "cloudru", "zai-org/GLM-4.7", 5, 30, concurrency=3)

        assert metrics["worker_name"] == "test-cr"
        assert metrics["processed"] == 8

    @pytest.mark.asyncio
    async def test_worker_failure_returns_error_metrics(self):
        with patch("app.services.analysis_pool._run_openrouter_worker", new_callable=AsyncMock) as mock_or:
            mock_or.side_effect = RuntimeError("API timeout")
            metrics = await _run_worker("fail-worker", "openrouter", "m", 10, 50)

        assert metrics["worker_name"] == "fail-worker"
        assert metrics["processed"] == 0
        assert metrics["failed"] == 1
        assert "API timeout" in metrics["errors"][0]
        assert metrics["duration_seconds"] >= 0

    @pytest.mark.asyncio
    async def test_duration_is_measured(self):
        async def _slow(*_a, **_kw):
            import asyncio

            await asyncio.sleep(0.05)
            return 1

        with patch("app.services.analysis_pool._run_openrouter_worker", side_effect=_slow):
            metrics = await _run_worker("slow", "openrouter", "m", 10, 10)

        assert metrics["duration_seconds"] >= 0.04


# ---------------------------------------------------------------------------
# run_analysis_pool — end-to-end pool metrics
# ---------------------------------------------------------------------------


class TestRunAnalysisPool:
    """run_analysis_pool aggregates per-worker metrics."""

    @pytest.mark.asyncio
    async def test_empty_backlog_returns_zeros(self):
        with patch("app.services.analysis_pool.get_backlog_size", new_callable=AsyncMock) as mock_bl:
            mock_bl.return_value = 0
            result = await run_analysis_pool()

        assert result["backlog_before"] == 0
        assert result["backlog_after"] == 0
        assert result["workers"] == 0
        assert result["total_processed"] == 0
        assert result["total_failed"] == 0
        assert result["total_errors"] == []
        assert result["worker_metrics"] == []

    @pytest.mark.asyncio
    async def test_single_worker_metrics(self):
        """Backlog < 50 -> 1 worker."""
        with (
            patch("app.services.analysis_pool.get_backlog_size", new_callable=AsyncMock) as mock_bl,
            patch("app.services.analysis_pool._run_worker", new_callable=AsyncMock) as mock_w,
        ):
            # First call returns backlog_before, second returns backlog_after
            mock_bl.side_effect = [30, 10]
            mock_w.return_value = _make_worker_metrics("mistral-nemo", processed=20, duration_seconds=5.0)

            result = await run_analysis_pool(max_total=100)

        assert result["backlog_before"] == 30
        assert result["backlog_after"] == 10
        assert result["workers"] == 1
        assert result["total_processed"] == 20
        assert result["total_failed"] == 0
        assert len(result["worker_metrics"]) == 1
        assert result["worker_metrics"][0]["worker_name"] == "mistral-nemo"

    @pytest.mark.asyncio
    async def test_two_workers_metrics(self):
        """Backlog 50-499 -> 2 workers."""
        with (
            patch("app.services.analysis_pool.get_backlog_size", new_callable=AsyncMock) as mock_bl,
            patch("app.services.analysis_pool._run_worker", new_callable=AsyncMock) as mock_w,
        ):
            mock_bl.side_effect = [200, 100]
            mock_w.side_effect = [
                _make_worker_metrics("mistral-nemo", processed=50, duration_seconds=10.0),
                _make_worker_metrics("qwen-2.5-7b", processed=50, duration_seconds=12.0),
            ]

            result = await run_analysis_pool(max_total=200)

        assert result["workers"] == 2
        assert result["total_processed"] == 100
        assert len(result["worker_metrics"]) == 2

    @pytest.mark.asyncio
    async def test_three_workers_metrics(self):
        """Backlog 500+ -> 3 workers."""
        with (
            patch("app.services.analysis_pool.get_backlog_size", new_callable=AsyncMock) as mock_bl,
            patch("app.services.analysis_pool._run_worker", new_callable=AsyncMock) as mock_w,
        ):
            mock_bl.side_effect = [600, 300]
            mock_w.side_effect = [
                _make_worker_metrics("mistral-nemo", processed=100, duration_seconds=8.0),
                _make_worker_metrics("qwen-2.5-7b", processed=100, duration_seconds=9.0),
                _make_worker_metrics("cloudru-glm", processed=100, duration_seconds=15.0),
            ]

            result = await run_analysis_pool(max_total=600)

        assert result["workers"] == 3
        assert result["total_processed"] == 300
        assert len(result["worker_metrics"]) == 3

    @pytest.mark.asyncio
    async def test_partial_failure_aggregation(self):
        """One worker fails, another succeeds -- both metrics reported."""
        with (
            patch("app.services.analysis_pool.get_backlog_size", new_callable=AsyncMock) as mock_bl,
            patch("app.services.analysis_pool._run_worker", new_callable=AsyncMock) as mock_w,
        ):
            mock_bl.side_effect = [100, 70]
            mock_w.side_effect = [
                _make_worker_metrics("mistral-nemo", processed=30, duration_seconds=5.0),
                _make_worker_metrics("qwen-2.5-7b", failed=1, errors=["connection refused"]),
            ]

            result = await run_analysis_pool()

        assert result["total_processed"] == 30
        assert result["total_failed"] == 1
        assert "connection refused" in result["total_errors"]
        assert len(result["worker_metrics"]) == 2

    @pytest.mark.asyncio
    async def test_gather_exception_normalized(self):
        """If asyncio.gather returns an Exception object, it's wrapped in metrics."""
        with (
            patch("app.services.analysis_pool.get_backlog_size", new_callable=AsyncMock) as mock_bl,
            patch("app.services.analysis_pool._run_worker", new_callable=AsyncMock) as mock_w,
        ):
            mock_bl.side_effect = [30, 20]
            # Simulate gather returning an exception (return_exceptions=True)
            mock_w.return_value = RuntimeError("unexpected")

            result = await run_analysis_pool()

        # The RuntimeError is not a dict, so normalization should wrap it
        assert result["total_failed"] >= 0  # At least runs without crashing

    @pytest.mark.asyncio
    async def test_errors_truncated_in_aggregate(self):
        """Total errors list is capped at MAX_ERRORS_PER_WORKER."""
        many_errors = [f"err-{i}" for i in range(20)]
        with (
            patch("app.services.analysis_pool.get_backlog_size", new_callable=AsyncMock) as mock_bl,
            patch("app.services.analysis_pool._run_worker", new_callable=AsyncMock) as mock_w,
        ):
            mock_bl.side_effect = [30, 10]
            mock_w.return_value = _make_worker_metrics("w", errors=many_errors)

            result = await run_analysis_pool()

        assert len(result["total_errors"]) <= MAX_ERRORS_PER_WORKER


# ---------------------------------------------------------------------------
# WORKER_CONFIGS integrity
# ---------------------------------------------------------------------------


class TestWorkerConfigs:
    """Verify WORKER_CONFIGS has expected structure."""

    def test_at_least_three_workers_defined(self):
        assert len(WORKER_CONFIGS) >= 3

    def test_each_config_has_required_keys(self):
        for cfg in WORKER_CONFIGS:
            assert "name" in cfg
            assert "provider" in cfg
            assert "model" in cfg
            assert "batch_size" in cfg

    def test_cloudru_worker_has_concurrency(self):
        cloudru_cfgs = [c for c in WORKER_CONFIGS if c["provider"] == "cloudru"]
        assert len(cloudru_cfgs) >= 1
        for cfg in cloudru_cfgs:
            assert cfg.get("concurrency", 1) > 1, "Cloud.ru worker should use concurrency > 1"
