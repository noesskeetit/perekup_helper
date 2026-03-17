"""Tests for listing page parser."""

from avito_parser.listing_parser import (
    SearchFilters,
    build_search_url,
    has_next_page,
    parse_listing_page,
)


class TestBuildSearchUrl:
    def test_basic_url(self):
        filters = SearchFilters(location_slug="moskva")
        url = build_search_url(filters)
        assert "moskva" in url
        assert "avtomobili" in url

    def test_with_brand_and_model(self):
        filters = SearchFilters(brand="Toyota", model="Camry", location_slug="moskva")
        url = build_search_url(filters)
        assert "toyota" in url
        assert "camry" in url

    def test_with_price_filters(self):
        filters = SearchFilters(price_from=500000, price_to=1500000)
        url = build_search_url(filters)
        assert "pmin=500000" in url
        assert "pmax=1500000" in url

    def test_with_year_filters(self):
        filters = SearchFilters(year_from=2018, year_to=2023)
        url = build_search_url(filters)
        assert "110000_from" in url
        assert "110000_to" in url
        assert "2018" in url
        assert "2023" in url

    def test_pagination(self):
        filters = SearchFilters()
        url_p1 = build_search_url(filters, page=1)
        url_p3 = build_search_url(filters, page=3)
        assert "p=" not in url_p1
        assert "p=3" in url_p3

    def test_brand_only(self):
        filters = SearchFilters(brand="BMW")
        url = build_search_url(filters)
        assert "bmw" in url
        assert url.count("/bmw") == 1


class TestParseListingPage:
    def test_parse_html_listing(self, mock_listing_html):
        items = parse_listing_page(mock_listing_html)
        assert len(items) == 3

        toyota = items[0]
        assert toyota.external_id == "12345"
        assert "toyota_camry" in toyota.url
        assert "Toyota Camry" in toyota.title
        assert toyota.price == 1500000

        bmw = items[1]
        assert bmw.external_id == "67890"
        assert bmw.price == 2300000

        kia = items[2]
        assert kia.external_id == "11111"
        assert kia.price == 950000

    def test_parse_json_listing(self, mock_listing_html_json):
        items = parse_listing_page(mock_listing_html_json)
        assert len(items) == 2

        honda = items[0]
        assert honda.external_id == "99001"
        assert honda.price == 1800000
        assert "honda_civic" in honda.url

    def test_empty_page(self):
        items = parse_listing_page("<html><body></body></html>")
        assert items == []


class TestHasNextPage:
    def test_has_next_page(self, mock_listing_html):
        assert has_next_page(mock_listing_html) is True

    def test_no_next_page(self):
        html = "<html><body><div>No pagination</div></body></html>"
        assert has_next_page(html) is False
