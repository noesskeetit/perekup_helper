import pytest


@pytest.mark.asyncio
async def test_health(async_client):
    resp = await async_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_index_page(async_client):
    resp = await async_client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert "PerekupHelper" in html
    assert "Toyota" in html
    assert "Camry" in html
    assert "BMW" in html


@pytest.mark.asyncio
async def test_index_htmx_returns_partial(async_client):
    resp = await async_client.get("/", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    html = resp.text
    assert "<table" in html
    assert "Toyota" in html


@pytest.mark.asyncio
async def test_index_empty(async_client_empty):
    resp = await async_client_empty.get("/")
    assert resp.status_code == 200
    assert "Объявления не найдены" in resp.text


@pytest.mark.asyncio
async def test_detail_page(async_detail_client, async_sample_listings):
    # First listing UUID
    listing_id = "11111111-1111-1111-1111-111111111111"
    resp = await async_detail_client.get(f"/listings/{listing_id}")
    assert resp.status_code == 200
    html = resp.text
    assert "Toyota" in html
    assert "Camry" in html
    assert "Открыть оригинал" in html


@pytest.mark.asyncio
async def test_detail_htmx_returns_partial(async_detail_client, async_sample_listings):
    listing_id = "11111111-1111-1111-1111-111111111111"
    resp = await async_detail_client.get(f"/listings/{listing_id}", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert "detail-card" in resp.text


@pytest.mark.asyncio
async def test_detail_not_found(async_detail_client_empty):
    resp = await async_detail_client_empty.get("/listings/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
    assert "не найдено" in resp.text


@pytest.mark.asyncio
async def test_color_indication(async_client):
    resp = await async_client.get("/")
    html = resp.text
    assert "row-good" in html
    assert "row-bad" in html


@pytest.mark.asyncio
async def test_category_badges(async_client):
    resp = await async_client.get("/")
    html = resp.text
    assert "cat-clean" in html
    assert "cat-damaged_body" in html


@pytest.mark.asyncio
async def test_score_display(async_client):
    """Scoring column shows confidence as a colored number."""
    resp = await async_client.get("/")
    html = resp.text
    assert "score-badge" in html


@pytest.mark.asyncio
async def test_diff_badges(async_client):
    resp = await async_client.get("/")
    html = resp.text
    # Sample data has price_diff_pct between -6.7 and -14.3 (all 5-15% below market → yellow)
    assert "diff-neutral" in html or "diff-good" in html or "diff-bad" in html


@pytest.mark.asyncio
async def test_filter_form_elements(async_client):
    resp = await async_client.get("/")
    html = resp.text
    assert 'name="brand"' in html
    assert 'name="car_model"' in html
    assert 'name="year_from"' in html
    assert 'name="category"' in html
    assert 'name="market_diff_pct_min"' in html
    assert "Ниже рынка на" in html
    assert "Применить" in html
    assert "Сбросить" in html


@pytest.mark.asyncio
async def test_market_diff_pct_min_filter(async_client):
    # Filter: at least 10% below market → price_diff_pct <= -10
    # Sample data: -11.8, -10.0, -14.3 qualify; -8.0 and -6.7 do not
    resp = await async_client.get("/", params={"market_diff_pct_min": "10"})
    assert resp.status_code == 200
    html = resp.text
    assert "Toyota" in html  # Camry (-11.8) qualifies
    assert "Kia" in html  # K5 (-10.0) qualifies


@pytest.mark.asyncio
async def test_photo_preview_in_table(async_client):
    """Table shows <img> for listings that have photos."""
    resp = await async_client.get("/")
    assert resp.status_code == 200
    html = resp.text
    # Toyota Camry (id[0]) has photos in conftest; expect an <img> tag
    assert "photo-cell" in html
    assert "<img" in html
    assert "example.com/photo1.jpg" in html


@pytest.mark.asyncio
async def test_no_photo_placeholder(async_client):
    """Table shows 'Нет фото' placeholder for listings without photos."""
    resp = await async_client.get("/")
    assert resp.status_code == 200
    # Other listings have photos=None → placeholder rendered
    assert "no-photo" in resp.text


@pytest.mark.asyncio
async def test_photo_gallery_in_detail(async_detail_client):
    """Detail card shows photo gallery when listing has photos."""
    listing_id = "11111111-1111-1111-1111-111111111111"  # Toyota Camry with photos
    resp = await async_detail_client.get(f"/listings/{listing_id}", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    html = resp.text
    assert "detail-photos" in html
    assert "<img" in html
    assert "example.com/photo1.jpg" in html


@pytest.mark.asyncio
async def test_no_gallery_without_photos(async_detail_client):
    """Detail card does not show gallery when listing has no photos."""
    listing_id = "22222222-2222-2222-2222-222222222222"  # RAV4 with photos=None
    resp = await async_detail_client.get(f"/listings/{listing_id}", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert "detail-photos" not in resp.text


@pytest.mark.asyncio
async def test_ai_analysis_in_detail(async_detail_client):
    """Detail card shows AI analysis block with summary and confidence."""
    listing_id = "11111111-1111-1111-1111-111111111111"  # Toyota Camry with analysis
    resp = await async_detail_client.get(f"/listings/{listing_id}", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    html = resp.text
    assert "ai-analysis" in html
    assert "AI-" in html  # AI-Анализ heading
    assert "95%" in html  # confidence=0.95


@pytest.mark.asyncio
async def test_flags_as_tags_in_detail(async_detail_client):
    """Detail card shows flags as tag badges, not a plain list."""
    listing_id = "33333333-3333-3333-3333-333333333333"  # BMW X5 with flags=["после ДТП"]
    resp = await async_detail_client.get(f"/listings/{listing_id}", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    html = resp.text
    assert "flag-tag" in html
    assert "после ДТП" in html


@pytest.mark.asyncio
async def test_price_diff_rubles_in_detail(async_detail_client):
    """Detail card shows price difference in rubles."""
    listing_id = "11111111-1111-1111-1111-111111111111"  # Toyota: price=1500000, market=1700000
    resp = await async_detail_client.get(f"/listings/{listing_id}", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    html = resp.text
    assert "price-diff-abs" in html
    assert "200" in html  # 200 000 ruble difference
