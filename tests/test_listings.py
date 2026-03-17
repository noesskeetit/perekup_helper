def test_list_listings_empty(client):
    resp = client.get("/listings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []
    assert data["page"] == 1


def test_list_listings_with_data(client, sample_listings):
    resp = client.get("/listings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert len(data["items"]) == 5


def test_filter_by_brand(client, sample_listings):
    resp = client.get("/listings", params={"brand": "Toyota"})
    data = resp.json()
    assert data["total"] == 2
    assert all("Toyota" in item["brand"] for item in data["items"])


def test_filter_by_model(client, sample_listings):
    resp = client.get("/listings", params={"model": "Camry"})
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["model"] == "Camry"


def test_filter_by_year_range(client, sample_listings):
    resp = client.get("/listings", params={"year_from": 2020, "year_to": 2021})
    data = resp.json()
    assert data["total"] == 2
    assert all(2020 <= item["year"] <= 2021 for item in data["items"])


def test_filter_by_price_range(client, sample_listings):
    resp = client.get("/listings", params={"price_from": 2_000_000, "price_to": 2_800_000})
    data = resp.json()
    assert data["total"] == 2
    for item in data["items"]:
        assert 2_000_000 <= item["price"] <= 2_800_000


def test_filter_by_mileage_range(client, sample_listings):
    resp = client.get("/listings", params={"mileage_from": 0, "mileage_to": 50_000})
    data = resp.json()
    assert data["total"] == 3
    for item in data["items"]:
        assert item["mileage"] <= 50_000


def test_filter_by_market_diff_pct(client, sample_listings):
    resp = client.get("/listings", params={"market_diff_pct": -5.0})
    data = resp.json()
    assert data["total"] == 3
    for item in data["items"]:
        assert item["market_diff_pct"] <= -5.0


def test_filter_by_category(client, sample_listings):
    resp = client.get("/listings", params={"category": "clean"})
    data = resp.json()
    assert data["total"] == 3
    assert all(item["category"] == "clean" for item in data["items"])


def test_sort_by_price_diff(client, sample_listings):
    resp = client.get("/listings", params={"sort_by": "price_diff"})
    data = resp.json()
    diffs = [item["price_diff"] for item in data["items"]]
    assert diffs == sorted(diffs)


def test_sort_by_score(client, sample_listings):
    resp = client.get("/listings", params={"sort_by": "score"})
    data = resp.json()
    scores = [item["score"] for item in data["items"]]
    assert scores == sorted(scores, reverse=True)


def test_sort_by_created_at(client, sample_listings):
    resp = client.get("/listings", params={"sort_by": "created_at"})
    data = resp.json()
    dates = [item["created_at"] for item in data["items"]]
    assert dates == sorted(dates, reverse=True)


def test_pagination(client, sample_listings):
    resp = client.get("/listings", params={"page": 1, "per_page": 2})
    data = resp.json()
    assert data["total"] == 5
    assert len(data["items"]) == 2
    assert data["page"] == 1
    assert data["per_page"] == 2
    assert data["pages"] == 3

    resp2 = client.get("/listings", params={"page": 3, "per_page": 2})
    data2 = resp2.json()
    assert len(data2["items"]) == 1


def test_pagination_invalid_page(client):
    resp = client.get("/listings", params={"page": 0})
    assert resp.status_code == 422


def test_get_listing_detail(client, sample_listings):
    listing_id = sample_listings[0].id
    resp = client.get(f"/listings/{listing_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == listing_id
    assert data["brand"] == "Toyota"
    assert data["model"] == "Camry"
    assert data["ai_analysis"] is not None
    assert "один владелец" in data["ai_analysis"]


def test_get_listing_not_found(client, sample_listings):
    resp = client.get("/listings/9999")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Объявление не найдено"


def test_combined_filters(client, sample_listings):
    resp = client.get(
        "/listings",
        params={
            "brand": "Toyota",
            "year_from": 2020,
            "category": "clean",
        },
    )
    data = resp.json()
    assert data["total"] == 2
    for item in data["items"]:
        assert "Toyota" in item["brand"]
        assert item["year"] >= 2020
        assert item["category"] == "clean"
