"""Tests for stats HTML page (async routes)."""

import pytest


@pytest.mark.asyncio
async def test_stats_html_page_renders(async_client):
    resp = await async_client.get("/stats")
    assert resp.status_code == 200
    html = resp.text
    assert "Статистика" in html


@pytest.mark.asyncio
async def test_stats_html_page_empty_db(async_client_empty):
    resp = await async_client_empty.get("/stats")
    assert resp.status_code == 200
