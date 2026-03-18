"""Tests for auto.ru listing page parser."""

import pytest

from autoru_parser.listing_parser import (
    SearchFilters,
    build_search_url,
    has_next_page,
    parse_listing_page,
)


@pytest.fixture
def mock_autoru_listing_html():
    """Mock auto.ru listing page with HTML listing items."""
    return """<!DOCTYPE html>
<html>
<head><title>Купить авто</title></head>
<body>
<article class="ListingItem">
    <a class="ListingItemTitle__link" href="/cars/used/sale/toyota/camry/1234567890-aabbccdd/">Toyota Camry 2020</a>
    <span class="Price__content">1 500 000 ₽</span>
</article>
<article class="ListingItem">
    <a class="ListingItemTitle__link" href="/cars/used/sale/bmw/x5/9876543210-11223344/">BMW X5 2019</a>
    <span class="Price__content">2 300 000 ₽</span>
</article>
<article class="ListingItem">
    <a class="ListingItemTitle__link" href="/cars/used/sale/kia/k5/1111111111-aabbccdd/">Kia K5 2022</a>
    <span class="Price__content">950 000 ₽</span>
</article>
<a class="ListingPagination__next" href="?page=2">Следующая</a>
</body>
</html>"""


@pytest.fixture
def mock_autoru_listing_html_json():
    """Mock auto.ru listing page with embedded JSON state."""
    return """<!DOCTYPE html>
<html>
<head>
<script id="initial-state" type="application/json">
{
    "listing": {
        "offers": [
            {
                "id": "2000000001-abc00001",
                "saleId": "2000000001-abc00001",
                "url": "https://auto.ru/cars/used/sale/honda/civic/2000000001-abc00001/",
                "vehicle_info": {
                    "mark": {"name": "Honda"},
                    "model": {"name": "Civic"},
                    "tech_param": {"year": 2021}
                },
                "price_info": {"price": 1800000}
            },
            {
                "id": "2000000002-abc00002",
                "saleId": "2000000002-abc00002",
                "url": "https://auto.ru/cars/used/sale/mazda/3/2000000002-abc00002/",
                "vehicle_info": {
                    "mark": {"name": "Mazda"},
                    "model": {"name": "3"},
                    "tech_param": {"year": 2020}
                },
                "price_info": {"price": 1600000}
            }
        ]
    }
}
</script>
</head>
<body></body>
</html>"""


class TestBuildSearchUrl:
    def test_basic_url(self):
        filters = SearchFilters()
        url = build_search_url(filters)
        assert "auto.ru" in url
        assert "/cars/used/list/" in url

    def test_with_brand(self):
        filters = SearchFilters(brand="Toyota")
        url = build_search_url(filters)
        assert "mark=TOYOTA" in url

    def test_with_brand_and_model(self):
        filters = SearchFilters(brand="Toyota", model="Camry")
        url = build_search_url(filters)
        assert "mark=TOYOTA" in url
        assert "model=CAMRY" in url

    def test_with_price_filters(self):
        filters = SearchFilters(price_from=500000, price_to=1500000)
        url = build_search_url(filters)
        assert "price_from=500000" in url
        assert "price_to=1500000" in url

    def test_with_year_filters(self):
        filters = SearchFilters(year_from=2018, year_to=2023)
        url = build_search_url(filters)
        assert "year_from=2018" in url
        assert "year_to=2023" in url

    def test_pagination(self):
        filters = SearchFilters()
        url_p1 = build_search_url(filters, page=1)
        url_p3 = build_search_url(filters, page=3)
        assert "page=" not in url_p1
        assert "page=3" in url_p3

    def test_location_slug_ignored(self):
        """location_slug kept for interface compat but not used in URL."""
        filters = SearchFilters(location_slug="moskva")
        url = build_search_url(filters)
        assert "moskva" not in url


class TestParseListingPage:
    def test_parse_html_listing(self, mock_autoru_listing_html):
        items = parse_listing_page(mock_autoru_listing_html)
        assert len(items) == 3

        toyota = items[0]
        assert toyota.external_id == "1234567890-aabbccdd"
        assert "toyota" in toyota.url
        assert "Toyota Camry" in toyota.title

        bmw = items[1]
        assert bmw.external_id == "9876543210-11223344"

        kia = items[2]
        assert kia.external_id == "1111111111-aabbccdd"

    def test_parse_json_listing(self, mock_autoru_listing_html_json):
        items = parse_listing_page(mock_autoru_listing_html_json)
        assert len(items) == 2

        honda = items[0]
        assert honda.external_id == "2000000001-abc00001"
        assert honda.price == 1800000
        assert "Honda" in honda.title

        mazda = items[1]
        assert mazda.external_id == "2000000002-abc00002"
        assert mazda.price == 1600000

    def test_empty_page(self):
        items = parse_listing_page("<html><body></body></html>")
        assert items == []


class TestHasNextPage:
    def test_has_next_page(self, mock_autoru_listing_html):
        assert has_next_page(mock_autoru_listing_html) is True

    def test_no_next_page(self):
        html = "<html><body><div>No pagination</div></body></html>"
        assert has_next_page(html) is False

    def test_next_page_from_json_flag(self):
        html = '<html><body><script>{"hasNextPage":true}</script></body></html>'
        assert has_next_page(html) is True
