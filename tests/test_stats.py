"""Tests for stats API endpoints and HTML page."""

import pytest

# ---------------------------------------------------------------------------
# /api/stats/summary
# ---------------------------------------------------------------------------


def test_summary_returns_correct_structure(sync_client, sample_listings):
    resp = sync_client.get("/api/stats/summary")
    assert resp.status_code == 200
    data = resp.json()
    expected_keys = {
        "total_listings",
        "total_unique",
        "by_source",
        "by_category",
        "avg_price",
        "median_price",
        "avg_discount",
        "listings_today",
        "listings_this_week",
    }
    assert expected_keys == set(data.keys())


def test_summary_total_matches_db(sync_client, sample_listings):
    resp = sync_client.get("/api/stats/summary")
    data = resp.json()
    assert data["total_listings"] == 5


def test_summary_by_source(sync_client, sample_listings):
    resp = sync_client.get("/api/stats/summary")
    data = resp.json()
    assert data["by_source"]["avito"] == 3
    assert data["by_source"]["autoru"] == 2


def test_summary_by_category(sync_client, sample_listings):
    resp = sync_client.get("/api/stats/summary")
    data = resp.json()
    assert data["by_category"]["clean"] == 3
    assert data["by_category"]["damaged_body"] == 2


def test_summary_avg_price(sync_client, sample_listings):
    resp = sync_client.get("/api/stats/summary")
    data = resp.json()
    # (1.5M + 2.3M + 2.8M + 1.8M + 1.2M) / 5 = 1_920_000
    assert data["avg_price"] == pytest.approx(1_920_000, rel=0.01)


def test_summary_median_price(sync_client, sample_listings):
    resp = sync_client.get("/api/stats/summary")
    data = resp.json()
    # Sorted prices: 1.2M, 1.5M, 1.8M, 2.3M, 2.8M → median = 1.8M
    assert data["median_price"] == pytest.approx(1_800_000, rel=0.01)


def test_summary_avg_discount(sync_client, sample_listings):
    resp = sync_client.get("/api/stats/summary")
    data = resp.json()
    # All market_diff_pct are negative: -11.8, -8.0, -6.7, -10.0, -14.3
    # avg = (-11.8 + -8.0 + -6.7 + -10.0 + -14.3) / 5 = -10.16
    assert data["avg_discount"] is not None
    assert data["avg_discount"] < 0


def test_summary_empty_db(sync_client_empty):
    resp = sync_client_empty.get("/api/stats/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_listings"] == 0
    assert data["total_unique"] == 0
    assert data["by_source"] == {}
    assert data["by_category"] == {}
    assert data["avg_price"] is None
    assert data["median_price"] is None
    assert data["avg_discount"] is None


def test_summary_listings_today(sync_client, sample_listings):
    resp = sync_client.get("/api/stats/summary")
    data = resp.json()
    # All sample listings created with datetime.utcnow() → all are today
    assert data["listings_today"] == 5


def test_summary_listings_this_week(sync_client, sample_listings):
    resp = sync_client.get("/api/stats/summary")
    data = resp.json()
    assert data["listings_this_week"] == 5


# ---------------------------------------------------------------------------
# /api/stats/brands
# ---------------------------------------------------------------------------


def test_brands_returns_sorted_list(sync_client, sample_listings):
    resp = sync_client.get("/api/stats/brands")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) > 0
    # Toyota has 2 listings → should be first
    assert data[0]["brand"] == "Toyota"
    assert data[0]["count"] == 2
    # Remaining brands have 1 each
    counts = [item["count"] for item in data]
    assert counts == sorted(counts, reverse=True)


def test_brands_has_avg_price(sync_client, sample_listings):
    resp = sync_client.get("/api/stats/brands")
    data = resp.json()
    toyota = next(b for b in data if b["brand"] == "Toyota")
    # Toyota: (1.5M + 2.3M) / 2 = 1_900_000
    assert toyota["avg_price"] == pytest.approx(1_900_000, rel=0.01)


def test_brands_has_avg_discount(sync_client, sample_listings):
    resp = sync_client.get("/api/stats/brands")
    data = resp.json()
    toyota = next(b for b in data if b["brand"] == "Toyota")
    # Toyota: (-11.8 + -8.0) / 2 = -9.9
    assert toyota["avg_discount"] is not None
    assert toyota["avg_discount"] < 0


def test_brands_empty_db(sync_client_empty):
    resp = sync_client_empty.get("/api/stats/brands")
    assert resp.status_code == 200
    data = resp.json()
    assert data == []


# ---------------------------------------------------------------------------
# /api/stats/price-distribution
# ---------------------------------------------------------------------------


def test_price_distribution_returns_buckets(sync_client, sample_listings):
    resp = sync_client.get("/api/stats/price-distribution")
    assert resp.status_code == 200
    data = resp.json()
    assert "buckets" in data
    assert isinstance(data["buckets"], list)
    total_count = sum(b["count"] for b in data["buckets"])
    assert total_count == 5


def test_price_distribution_bucket_format(sync_client, sample_listings):
    resp = sync_client.get("/api/stats/price-distribution")
    data = resp.json()
    for bucket in data["buckets"]:
        assert "range" in bucket
        assert "count" in bucket
        assert bucket["count"] > 0


def test_price_distribution_empty_db(sync_client_empty):
    resp = sync_client_empty.get("/api/stats/price-distribution")
    assert resp.status_code == 200
    data = resp.json()
    assert data["buckets"] == []


# ---------------------------------------------------------------------------
# HTML /stats page
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Legacy /stats endpoint (backward compatibility)
# ---------------------------------------------------------------------------


def test_stats_empty(sync_client_empty):
    resp = sync_client_empty.get("/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_listings"] == 0
    assert data["avg_price"] is None
    assert data["by_category"] == {}
    assert data["by_brand"] == {}


def test_stats_with_data(sync_client, sample_listings):
    resp = sync_client.get("/stats")
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


def test_stats_brand_counts(sync_client, sample_listings):
    resp = sync_client.get("/stats")
    data = resp.json()
    total_by_brand = sum(data["by_brand"].values())
    assert total_by_brand == 5
