"""Tests for ALE-27: Dashboard layout — grid structure, sidebar stats,
filter persistence (localStorage), and responsive CSS classes."""

import pytest

# ---------------------------------------------------------------------------
# Dashboard layout structure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dashboard_layout_wrapper(async_client):
    """Main page has .dashboard-layout grid wrapper."""
    resp = await async_client.get("/")
    assert resp.status_code == 200
    assert "dashboard-layout" in resp.text


@pytest.mark.asyncio
async def test_dashboard_main_section(async_client):
    """Main content area wrapped in .dashboard-main."""
    resp = await async_client.get("/")
    assert "dashboard-main" in resp.text


@pytest.mark.asyncio
async def test_dashboard_sidebar_section(async_client):
    """Page includes .dashboard-sidebar with quick stats."""
    resp = await async_client.get("/")
    assert "dashboard-sidebar" in resp.text


@pytest.mark.asyncio
async def test_dashboard_filters_section(async_client):
    """Filters panel is in .dashboard-filters."""
    resp = await async_client.get("/")
    assert "dashboard-filters" in resp.text


# ---------------------------------------------------------------------------
# Sidebar stats content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sidebar_shows_total(async_client):
    """Sidebar displays total count of listings."""
    resp = await async_client.get("/")
    html = resp.text
    assert "sidebar-stat" in html
    assert "Всего" in html


@pytest.mark.asyncio
async def test_sidebar_shows_avg_price(async_client):
    """Sidebar displays average price."""
    resp = await async_client.get("/")
    html = resp.text
    assert "Средняя цена" in html


@pytest.mark.asyncio
async def test_sidebar_shows_avg_discount(async_client):
    """Sidebar displays average discount percentage."""
    resp = await async_client.get("/")
    html = resp.text
    assert "Средняя скидка" in html


@pytest.mark.asyncio
async def test_sidebar_shows_category_counts(async_client):
    """Sidebar shows category distribution."""
    resp = await async_client.get("/")
    html = resp.text
    assert "sidebar-categories" in html
    assert "cat-clean" in html or "Чистая" in html


@pytest.mark.asyncio
async def test_sidebar_stats_grid(async_client):
    """Sidebar stats use .sidebar-stats-grid for 3/2/1 responsive layout."""
    resp = await async_client.get("/")
    assert "sidebar-stats-grid" in resp.text


# ---------------------------------------------------------------------------
# Filter persistence (localStorage)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_localstorage_script_present(async_client):
    """Page includes JavaScript for localStorage filter persistence."""
    resp = await async_client.get("/")
    html = resp.text
    assert "localStorage" in html


@pytest.mark.asyncio
async def test_localstorage_save_filters(async_client):
    """JS code saves filter values to localStorage on submit."""
    resp = await async_client.get("/")
    html = resp.text
    assert "saveFilters" in html


@pytest.mark.asyncio
async def test_localstorage_restore_filters(async_client):
    """JS code restores filter values from localStorage on load."""
    resp = await async_client.get("/")
    html = resp.text
    assert "restoreFilters" in html


@pytest.mark.asyncio
async def test_localstorage_clear_on_reset(async_client):
    """Reset button clears saved filters from localStorage."""
    resp = await async_client.get("/")
    html = resp.text
    assert "clearFilters" in html


# ---------------------------------------------------------------------------
# Responsive CSS classes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_responsive_css_breakpoints():
    """style.css contains responsive breakpoints for 3/2/1 grid."""
    import pathlib

    css_path = pathlib.Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "style.css"
    css = css_path.read_text(encoding="utf-8")

    # Desktop grid (3-column stats grid)
    assert "sidebar-stats-grid" in css
    # Tablet breakpoint
    assert "768px" in css
    # Desktop breakpoint
    assert "1200px" in css
    # Mobile: grid-template-columns with 1fr
    assert "1fr" in css


@pytest.mark.asyncio
async def test_dashboard_layout_in_css():
    """style.css contains .dashboard-layout grid rules."""
    import pathlib

    css_path = pathlib.Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "style.css"
    css = css_path.read_text(encoding="utf-8")

    assert ".dashboard-layout" in css
    assert ".dashboard-main" in css
    assert ".dashboard-sidebar" in css


# ---------------------------------------------------------------------------
# HTMX partial preserves layout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_htmx_partial_no_layout_wrapper(async_client):
    """HTMX partial response does NOT include dashboard-layout wrapper."""
    resp = await async_client.get("/", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    html = resp.text
    # Partial should be just the table, not the full layout
    assert "dashboard-layout" not in html
    assert "<table" in html


# ---------------------------------------------------------------------------
# Empty DB edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sidebar_empty_db(async_client_empty):
    """Sidebar renders without errors on empty database."""
    resp = await async_client_empty.get("/")
    assert resp.status_code == 200
    assert "dashboard-sidebar" in resp.text
    assert "Всего" in resp.text
