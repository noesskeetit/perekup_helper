import pytest


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_index_page(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert "Perekup Dashboard" in html
    assert "Toyota" in html
    assert "Camry" in html
    assert "BMW" in html


@pytest.mark.asyncio
async def test_index_htmx_returns_partial(client):
    resp = await client.get("/", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    html = resp.text
    assert "<table" in html
    assert "Toyota" in html


@pytest.mark.asyncio
async def test_index_empty(client_empty):
    resp = await client_empty.get("/")
    assert resp.status_code == 200
    assert "Объявления не найдены" in resp.text


@pytest.mark.asyncio
async def test_detail_page(detail_client, sample_listings):
    listing_id = sample_listings[0].id
    resp = await detail_client.get(f"/listings/{listing_id}")
    assert resp.status_code == 200
    html = resp.text
    assert "Toyota" in html
    assert "Camry" in html
    assert "Открыть оригинал" in html


@pytest.mark.asyncio
async def test_detail_htmx_returns_partial(detail_client, sample_listings):
    listing_id = sample_listings[0].id
    resp = await detail_client.get(f"/listings/{listing_id}", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert "detail-card" in resp.text


@pytest.mark.asyncio
async def test_detail_not_found(detail_client_empty):
    resp = await detail_client_empty.get("/listings/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
    assert "не найдено" in resp.text


@pytest.mark.asyncio
async def test_color_indication(client):
    resp = await client.get("/")
    html = resp.text
    assert "row-good" in html
    assert "row-bad" in html


@pytest.mark.asyncio
async def test_category_badges(client):
    resp = await client.get("/")
    html = resp.text
    assert "cat-clean" in html
    assert "cat-damaged_body" in html


@pytest.mark.asyncio
async def test_scoring_bar(client):
    resp = await client.get("/")
    html = resp.text
    assert "scoring-bar" in html
    assert "scoring-fill" in html


@pytest.mark.asyncio
async def test_diff_badges(client):
    resp = await client.get("/")
    html = resp.text
    assert "diff-good" in html or "diff-bad" in html


@pytest.mark.asyncio
async def test_filter_form_elements(client):
    resp = await client.get("/")
    html = resp.text
    assert 'name="brand"' in html
    assert 'name="car_model"' in html
    assert 'name="year_from"' in html
    assert 'name="category"' in html
    assert "Применить" in html
    assert "Сбросить" in html
