"""Tests for ALE-31: Card layout with grid/list view toggle for listings."""

import pathlib

import pytest

# ---------------------------------------------------------------------------
# View toggle buttons
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_view_toggle_buttons_present(async_client):
    """Dashboard has grid/list view toggle buttons."""
    resp = await async_client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert "view-toggle" in html
    assert 'data-view="grid"' in html
    assert 'data-view="list"' in html


# ---------------------------------------------------------------------------
# Cards grid container
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cards_grid_container(async_client):
    """Dashboard contains a cards-grid container for card view."""
    resp = await async_client.get("/")
    assert resp.status_code == 200
    assert "cards-grid" in resp.text


# ---------------------------------------------------------------------------
# Card elements
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_listing_card_elements(async_client):
    """Each listing card contains photo, price, market price, category badge, diff badge."""
    resp = await async_client.get("/")
    html = resp.text
    # Card container
    assert "listing-card" in html
    # Card photo
    assert "card-photo" in html
    # Card body with prices
    assert "card-price" in html
    assert "card-market-price" in html
    # Category and diff badges
    assert "category-badge" in html
    assert "diff-badge" in html


@pytest.mark.asyncio
async def test_card_shows_listing_data(async_client):
    """Card renders actual listing data (brand, price, etc.)."""
    resp = await async_client.get("/")
    html = resp.text
    assert "Toyota" in html
    assert "Camry" in html
    # Price 1 500 000 should appear
    assert "1 500 000" in html


@pytest.mark.asyncio
async def test_card_photo_rendered(async_client):
    """Card shows photo for listings that have photos."""
    resp = await async_client.get("/")
    html = resp.text
    assert "example.com/photo1.jpg" in html


# ---------------------------------------------------------------------------
# HTMX partial
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cards_in_htmx_partial(async_client):
    """HTMX partial response includes cards grid."""
    resp = await async_client.get("/", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    html = resp.text
    assert "cards-grid" in html
    assert "listing-card" in html


# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_card_empty_state(async_client_empty):
    """Empty cards grid shows appropriate message."""
    resp = await async_client_empty.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert "cards-grid" in html
    # Should have empty state text
    assert "Объявления не найдены" in html


# ---------------------------------------------------------------------------
# View preference localStorage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_view_preference_localstorage(async_client):
    """Page includes JS for saving/restoring view preference in localStorage."""
    resp = await async_client.get("/")
    html = resp.text
    assert "perekup_view" in html
    assert "localStorage" in html


@pytest.mark.asyncio
async def test_view_toggle_afterswap(async_client):
    """Page includes HTMX afterSwap handler to restore view preference."""
    resp = await async_client.get("/")
    html = resp.text
    assert "htmx:afterSwap" in html or "afterSwap" in html


# ---------------------------------------------------------------------------
# CSS: responsive card grid
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_card_grid_css():
    """style.css contains card grid responsive rules."""
    css_path = pathlib.Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "style.css"
    css = css_path.read_text(encoding="utf-8")
    assert ".cards-grid" in css
    assert ".listing-card" in css
    # 3-column desktop
    assert "1024px" in css
    # responsive columns
    assert "grid-template-columns" in css


@pytest.mark.asyncio
async def test_view_toggle_css():
    """style.css contains view toggle button styles."""
    css_path = pathlib.Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "style.css"
    css = css_path.read_text(encoding="utf-8")
    assert ".view-toggle" in css
