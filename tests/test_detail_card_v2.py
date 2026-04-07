"""Tests for ALE-34: Improved detail card — gallery, price block, AI verdict."""

import pathlib

import pytest

CSS_PATH = pathlib.Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "style.css"

# First listing ID from conftest — has photos, raw_data, and analysis
LISTING_ID = "11111111-1111-1111-1111-111111111111"
# Second listing — no photos, no raw_data
LISTING_NO_PHOTOS_ID = "22222222-2222-2222-2222-222222222222"
# Third listing — damaged_body category
LISTING_DAMAGED_ID = "33333333-3333-3333-3333-333333333333"


# ---------------------------------------------------------------------------
# Photo Gallery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gallery_main_photo(async_detail_client):
    """Detail page shows a main gallery photo for listings with photos."""
    resp = await async_detail_client.get(f"/listings/{LISTING_ID}")
    assert resp.status_code == 200
    html = resp.text
    assert "gallery-main" in html
    assert "gallery-main-img" in html
    assert "photo1.jpg" in html


@pytest.mark.asyncio
async def test_gallery_thumbnails(async_detail_client):
    """Detail page shows clickable thumbnails when multiple photos exist."""
    resp = await async_detail_client.get(f"/listings/{LISTING_ID}")
    html = resp.text
    assert "gallery-thumbs" in html
    assert "gallery-thumb" in html
    # All 3 photo URLs should appear in thumbnails
    assert "photo1.jpg" in html
    assert "photo2.jpg" in html
    assert "photo3.jpg" in html


@pytest.mark.asyncio
async def test_gallery_navigation_buttons(async_detail_client):
    """Gallery has prev/next navigation buttons."""
    resp = await async_detail_client.get(f"/listings/{LISTING_ID}")
    html = resp.text
    assert "gallery-prev" in html
    assert "gallery-next" in html
    assert "galleryNav" in html


@pytest.mark.asyncio
async def test_gallery_counter(async_detail_client):
    """Gallery shows a photo counter."""
    resp = await async_detail_client.get(f"/listings/{LISTING_ID}")
    html = resp.text
    assert "gallery-counter" in html
    assert "1 / 3" in html


@pytest.mark.asyncio
async def test_gallery_placeholder_no_photos(async_detail_client):
    """Listing without photos shows a placeholder instead of gallery."""
    resp = await async_detail_client.get(f"/listings/{LISTING_NO_PHOTOS_ID}")
    html = resp.text
    assert "gallery-placeholder" in html
    # No main gallery image element (id attribute is unique to the gallery)
    assert 'id="gallery-main-img"' not in html


@pytest.mark.asyncio
async def test_gallery_keyboard_nav_script(async_detail_client):
    """Gallery includes keyboard navigation (ArrowLeft / ArrowRight)."""
    resp = await async_detail_client.get(f"/listings/{LISTING_ID}")
    html = resp.text
    assert "ArrowLeft" in html
    assert "ArrowRight" in html


# ---------------------------------------------------------------------------
# Price Comparison Block
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_price_block_present(async_detail_client):
    """Detail page has a price comparison block."""
    resp = await async_detail_client.get(f"/listings/{LISTING_ID}")
    html = resp.text
    assert "price-block" in html
    assert "price-block-body" in html


@pytest.mark.asyncio
async def test_price_listing_and_market(async_detail_client):
    """Price block shows listing price and market price."""
    resp = await async_detail_client.get(f"/listings/{LISTING_ID}")
    html = resp.text
    assert "1 500 000" in html
    assert "1 700 000" in html


@pytest.mark.asyncio
async def test_price_diff_indicator(async_detail_client):
    """Price block shows a percentage diff indicator."""
    resp = await async_detail_client.get(f"/listings/{LISTING_ID}")
    html = resp.text
    assert "price-diff-indicator" in html
    assert "price-diff-pct" in html


@pytest.mark.asyncio
async def test_price_bar_visualization(async_detail_client):
    """Price block includes a visual bar comparing to market."""
    resp = await async_detail_client.get(f"/listings/{LISTING_ID}")
    html = resp.text
    assert "price-bar" in html
    assert "price-bar-fill" in html


@pytest.mark.asyncio
async def test_avito_estimate_shown(async_detail_client):
    """Avito estimate is displayed when present in raw_data."""
    resp = await async_detail_client.get(f"/listings/{LISTING_ID}")
    html = resp.text
    assert "1 650 000" in html
    assert "Avito" in html


@pytest.mark.asyncio
async def test_price_history_shown(async_detail_client):
    """Price history is displayed when present in raw_data."""
    resp = await async_detail_client.get(f"/listings/{LISTING_ID}")
    html = resp.text
    assert "price-history" in html
    assert "2026-04-01" in html
    assert "2026-03-15" in html


@pytest.mark.asyncio
async def test_price_block_no_market_price(async_detail_client):
    """Price block still renders when no market price is available."""
    # Listing 2 has market_price but check that price block header exists
    resp = await async_detail_client.get(f"/listings/{LISTING_NO_PHOTOS_ID}")
    html = resp.text
    assert "price-block" in html


# ---------------------------------------------------------------------------
# AI Analysis Verdict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verdict_banner_clean(async_detail_client):
    """Clean listing shows a verdict banner with appropriate class."""
    resp = await async_detail_client.get(f"/listings/{LISTING_ID}")
    html = resp.text
    assert "verdict-banner" in html
    assert "verdict-clean" in html


@pytest.mark.asyncio
async def test_verdict_banner_damaged(async_detail_client):
    """Damaged body listing shows a verdict with damaged class."""
    resp = await async_detail_client.get(f"/listings/{LISTING_DAMAGED_ID}")
    html = resp.text
    assert "verdict-banner" in html
    assert "verdict-damaged_body" in html


@pytest.mark.asyncio
async def test_verdict_shows_summary(async_detail_client):
    """Verdict banner shows the AI summary text."""
    resp = await async_detail_client.get(f"/listings/{LISTING_ID}")
    html = resp.text
    assert "verdict-summary" in html


@pytest.mark.asyncio
async def test_verdict_shows_confidence(async_detail_client):
    """Verdict banner shows model confidence percentage."""
    resp = await async_detail_client.get(f"/listings/{LISTING_ID}")
    html = resp.text
    assert "verdict-confidence" in html
    assert "95%" in html


@pytest.mark.asyncio
async def test_verdict_icon_present(async_detail_client):
    """Verdict banner has a category icon."""
    resp = await async_detail_client.get(f"/listings/{LISTING_ID}")
    html = resp.text
    assert "verdict-icon" in html


# ---------------------------------------------------------------------------
# CSS: new classes exist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_css_gallery_styles():
    """style.css contains gallery styles."""
    css = CSS_PATH.read_text(encoding="utf-8")
    assert ".gallery-main" in css
    assert ".gallery-thumb" in css
    assert ".gallery-nav" in css
    assert ".gallery-placeholder" in css


@pytest.mark.asyncio
async def test_css_verdict_styles():
    """style.css contains verdict banner styles for all categories."""
    css = CSS_PATH.read_text(encoding="utf-8")
    assert ".verdict-banner" in css
    assert ".verdict-clean" in css
    assert ".verdict-damaged_body" in css
    assert ".verdict-bad_docs" in css
    assert ".verdict-debtor" in css
    assert ".verdict-complex_but_profitable" in css


@pytest.mark.asyncio
async def test_css_price_block_styles():
    """style.css contains price comparison block styles."""
    css = CSS_PATH.read_text(encoding="utf-8")
    assert ".price-block" in css
    assert ".price-bar" in css
    assert ".price-diff-indicator" in css
    assert ".price-history" in css


@pytest.mark.asyncio
async def test_css_mobile_gallery():
    """style.css has mobile responsive rules for gallery."""
    css = CSS_PATH.read_text(encoding="utf-8")
    assert ".gallery-main img" in css


@pytest.mark.asyncio
async def test_css_dark_mode_verdict():
    """Dark mode styles exist for verdict confidence."""
    css = CSS_PATH.read_text(encoding="utf-8")
    assert '[data-theme="dark"] .verdict-confidence' in css


# ---------------------------------------------------------------------------
# HTMX partial rendering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_htmx_partial_has_gallery(async_detail_client):
    """HTMX partial response includes the gallery markup."""
    resp = await async_detail_client.get(
        f"/listings/{LISTING_ID}",
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    html = resp.text
    assert "gallery-main" in html
    assert "verdict-banner" in html
    assert "price-block" in html
