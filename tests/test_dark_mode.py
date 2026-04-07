"""Tests for ALE-32: Dark mode toggle — toggle button, CSS variables,
localStorage persistence, and theme-aware styling."""

import pathlib

import pytest

CSS_PATH = pathlib.Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "style.css"

# ---------------------------------------------------------------------------
# Toggle button presence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_theme_toggle_button_present(async_client):
    """Dashboard has a theme toggle button in the header."""
    resp = await async_client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert "theme-toggle" in html
    assert "theme-icon" in html


@pytest.mark.asyncio
async def test_theme_toggle_on_stats_page(async_client):
    """Stats page also has the theme toggle button."""
    resp = await async_client.get("/stats")
    assert resp.status_code == 200
    html = resp.text
    assert "theme-toggle" in html


@pytest.mark.asyncio
async def test_theme_toggle_aria_label(async_client):
    """Theme toggle button has an accessible aria-label."""
    resp = await async_client.get("/")
    html = resp.text
    assert "aria-label=" in html
    assert "theme-toggle" in html


# ---------------------------------------------------------------------------
# CSS custom properties for both themes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_css_has_root_variables():
    """style.css defines CSS custom properties in :root for light theme."""
    css = CSS_PATH.read_text(encoding="utf-8")
    assert ":root" in css
    assert "--bg:" in css
    assert "--surface:" in css
    assert "--border:" in css
    assert "--text:" in css
    assert "--text-muted:" in css
    assert "--primary:" in css


@pytest.mark.asyncio
async def test_css_has_dark_theme_overrides():
    """style.css defines [data-theme='dark'] overrides for all key variables."""
    css = CSS_PATH.read_text(encoding="utf-8")
    assert '[data-theme="dark"]' in css
    # Check that dark theme overrides key variables
    # Find the dark theme block and verify it contains variable overrides
    dark_idx = css.index('[data-theme="dark"]')
    dark_block = css[dark_idx : css.index("}", dark_idx) + 1]
    assert "--bg:" in dark_block
    assert "--surface:" in dark_block
    assert "--border:" in dark_block
    assert "--text:" in dark_block
    assert "--primary:" in dark_block


@pytest.mark.asyncio
async def test_css_dark_palette_values():
    """Dark theme uses appropriately dark background colors."""
    css = CSS_PATH.read_text(encoding="utf-8")
    dark_idx = css.index('[data-theme="dark"]')
    dark_block = css[dark_idx : css.index("}", dark_idx) + 1]
    # Background should be a dark color (starts with #1 or #2)
    assert "#1a1a2e" in dark_block or "#16213e" in dark_block


@pytest.mark.asyncio
async def test_css_theme_toggle_styles():
    """style.css contains .theme-toggle button styles."""
    css = CSS_PATH.read_text(encoding="utf-8")
    assert ".theme-toggle" in css


@pytest.mark.asyncio
async def test_css_no_hardcoded_table_header_bg():
    """Table and stats headers use CSS variables, not hardcoded colors."""
    css = CSS_PATH.read_text(encoding="utf-8")
    # The .listings-table th and .stats-table th should use var(--surface-alt)
    assert "--surface-alt:" in css
    assert "var(--surface-alt)" in css


# ---------------------------------------------------------------------------
# localStorage theme persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_localstorage_theme_script(async_client):
    """Page includes JavaScript for localStorage theme persistence."""
    resp = await async_client.get("/")
    html = resp.text
    assert "localStorage" in html
    assert "perekup_theme" in html


@pytest.mark.asyncio
async def test_theme_applied_before_render(async_client):
    """Theme is applied in <head> before CSS loads to prevent flash."""
    resp = await async_client.get("/")
    html = resp.text
    # The theme initialization script should appear before the CSS link
    theme_script_pos = html.index("perekup_theme")
    css_link_pos = html.index("style.css")
    assert theme_script_pos < css_link_pos


@pytest.mark.asyncio
async def test_theme_toggle_uses_data_attribute(async_client):
    """Theme toggle sets data-theme attribute on html element."""
    resp = await async_client.get("/")
    html = resp.text
    assert "data-theme" in html
    assert "setAttribute" in html or "removeAttribute" in html


# ---------------------------------------------------------------------------
# Theme transition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_css_theme_transition():
    """style.css includes smooth transition class for theme changes."""
    css = CSS_PATH.read_text(encoding="utf-8")
    assert "theme-transitioning" in css


# ---------------------------------------------------------------------------
# Dark mode form inputs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_css_dark_mode_inputs():
    """Dark mode styles filter inputs for readability."""
    css = CSS_PATH.read_text(encoding="utf-8")
    assert '[data-theme="dark"] .filter-group input' in css
    assert '[data-theme="dark"] .filter-group select' in css
