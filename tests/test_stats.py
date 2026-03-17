def test_stats_empty(client):
    resp = client.get("/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_listings"] == 0
    assert data["avg_price"] is None
    assert data["by_category"] == {}
    assert data["by_brand"] == {}


def test_stats_with_data(client, sample_listings):
    resp = client.get("/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_listings"] == 5
    assert data["avg_price"] is not None
    assert data["avg_price"] > 0
    assert data["avg_mileage"] is not None
    assert data["avg_market_diff_pct"] is not None
    assert data["avg_score"] is not None
    assert "clean" in data["by_category"]
    assert data["by_category"]["clean"] == 3
    assert "Toyota" in data["by_brand"]
    assert data["by_brand"]["Toyota"] == 2


def test_stats_brand_counts(client, sample_listings):
    resp = client.get("/stats")
    data = resp.json()
    total_by_brand = sum(data["by_brand"].values())
    assert total_by_brand == 5
