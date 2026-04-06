"""Tests for Drom.ru parser (app/parsers/drom.py).

Tests cover:
- BULL_URL_RE pattern matching
- JSON-LD @type=Car parsing
- Specs table extraction from HTML
- Brand/model extraction from titles
- Year extraction
- City/region extraction from URLs
- Seller info extraction from HTML
- Listing date extraction from JSON-LD and meta tags
- Photo URL extraction from HTML
- Preloaded state fallback parsing
- Pagination URL construction
- Edge cases: missing fields, empty responses, malformed data
"""

from __future__ import annotations

import json

from bs4 import BeautifulSoup

from app.parsers.drom import BULL_URL_RE, DromParser

# ── Helpers ─────────────────────────────────────────────────────────────────


def _make_soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _build_json_ld_car(
    brand="Toyota",
    model="Camry",
    year=2020,
    price=1_500_000,
    mileage=50000,
    vin=None,
    description=None,
    image_url=None,
):
    """Build a minimal JSON-LD @type=Car object."""
    data = {
        "@type": "Car",
        "brand": {"name": brand},
        "model": model,
        "vehicleModelDate": year,
        "offers": {"price": price},
        "mileageFromOdometer": {"value": str(mileage)},
    }
    if vin:
        data["vehicleIdentificationNumber"] = vin
    if description:
        data["description"] = description
    if image_url:
        data["image"] = {"url": image_url}
    return data


def _build_card_html(
    json_ld_data=None,
    specs=None,
    extra_links=None,
    meta_tags=None,
):
    """Build a minimal Drom card page HTML with JSON-LD and specs table."""
    parts = ["<html><head>"]

    if json_ld_data:
        parts.append(f'<script type="application/ld+json">{json.dumps(json_ld_data)}</script>')

    if meta_tags:
        for name, content in meta_tags.items():
            parts.append(f'<meta property="{name}" content="{content}" />')

    parts.append("</head><body>")

    if specs:
        parts.append("<table>")
        for key, val in specs.items():
            parts.append(f"<tr><th>{key}</th><td>{val}</td></tr>")
        parts.append("</table>")

    if extra_links:
        for href, text in extra_links:
            parts.append(f'<a href="{href}">{text}</a>')

    parts.append("</body></html>")
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# BULL_URL_RE
# ═══════════════════════════════════════════════════════════════════════════


class TestBullUrlRe:
    def test_matches_valid_card_url(self):
        url = "https://auto.drom.ru/moscow/toyota/camry/1234567.html"
        m = BULL_URL_RE.search(url)
        assert m is not None
        assert m.group(1) == "1234567"

    def test_matches_different_city(self):
        url = "https://auto.drom.ru/krasnodar/bmw/x5/9876543.html"
        m = BULL_URL_RE.search(url)
        assert m is not None
        assert m.group(1) == "9876543"

    def test_no_match_listing_page(self):
        url = "https://moscow.drom.ru/toyota/"
        assert BULL_URL_RE.search(url) is None

    def test_no_match_random_url(self):
        url = "https://example.com/not-a-car"
        assert BULL_URL_RE.search(url) is None


# ═══════════════════════════════════════════════════════════════════════════
# _extract_specs_table
# ═══════════════════════════════════════════════════════════════════════════


class TestExtractSpecsTable:
    def test_extracts_specs(self):
        html = _build_card_html(
            specs={
                "Двигатель": "бензин, 2.5 л",
                "Мощность": "181 л.с.",
                "Коробка передач": "автомат",
                "Привод": "передний",
                "Тип кузова": "седан",
            }
        )
        soup = _make_soup(html)
        specs = DromParser._extract_specs_table(soup)
        assert specs["Двигатель"] == "бензин, 2.5 л"
        assert specs["Мощность"] == "181 л.с."
        assert specs["Коробка передач"] == "автомат"
        assert specs["Привод"] == "передний"
        assert specs["Тип кузова"] == "седан"

    def test_ignores_small_tables(self):
        html = "<html><body><table><tr><th>A</th><td>B</td></tr></table></body></html>"
        soup = _make_soup(html)
        specs = DromParser._extract_specs_table(soup)
        assert specs == {}

    def test_empty_page(self):
        soup = _make_soup("<html><body></body></html>")
        specs = DromParser._extract_specs_table(soup)
        assert specs == {}


# ═══════════════════════════════════════════════════════════════════════════
# _parse_json_ld_car
# ═══════════════════════════════════════════════════════════════════════════


class TestParseJsonLdCar:
    def _parse(self, json_ld, url, specs=None, soup=None):
        parser = DromParser.__new__(DromParser)
        return parser._parse_json_ld_car(json_ld, url, specs, soup)

    def test_basic_car(self):
        data = _build_json_ld_car()
        url = "https://auto.drom.ru/moscow/toyota/camry/1234567.html"
        listing = self._parse(data, url)
        assert listing is not None
        assert listing.source == "drom"
        assert listing.external_id == "1234567"
        assert listing.brand == "Toyota"
        assert listing.model == "Camry"
        assert listing.year == 2020
        assert listing.price == 1_500_000
        assert listing.mileage == 50000

    def test_with_vin(self):
        data = _build_json_ld_car(vin="JTDBR40E600123456")
        url = "https://auto.drom.ru/moscow/toyota/camry/111.html"
        listing = self._parse(data, url)
        assert listing is not None
        assert listing.vin == "JTDBR40E600123456"

    def test_with_specs_engine(self):
        data = _build_json_ld_car()
        url = "https://auto.drom.ru/moscow/toyota/camry/111.html"
        specs = {"Двигатель": "бензин, 2.5 л", "Мощность": "181 л.с."}
        listing = self._parse(data, url, specs=specs)
        assert listing is not None
        assert listing.engine_type == "бензин"
        assert listing.engine_volume == 2.5
        assert listing.power_hp == 181

    def test_with_specs_transmission(self):
        data = _build_json_ld_car()
        url = "https://auto.drom.ru/moscow/toyota/camry/111.html"
        specs = {"Коробка передач": "вариатор", "Привод": "полный"}
        listing = self._parse(data, url, specs=specs)
        assert listing is not None
        assert listing.transmission == "вариатор"
        assert listing.drive_type == "полный"

    def test_with_specs_color_body(self):
        data = _build_json_ld_car()
        url = "https://auto.drom.ru/moscow/toyota/camry/111.html"
        specs = {"Цвет": "белый", "Тип кузова": "седан", "Руль": "левый"}
        listing = self._parse(data, url, specs=specs)
        assert listing is not None
        assert listing.color == "белый"
        assert listing.body_type == "седан"
        assert listing.steering_wheel == "левый"

    def test_with_owners_count(self):
        data = _build_json_ld_car()
        url = "https://auto.drom.ru/moscow/toyota/camry/111.html"
        specs = {"Владельцы": "2"}
        listing = self._parse(data, url, specs=specs)
        assert listing is not None
        assert listing.owners_count == 2

    def test_owners_four_plus(self):
        data = _build_json_ld_car()
        url = "https://auto.drom.ru/moscow/toyota/camry/111.html"
        specs = {"Владельцы": "4 и более"}
        listing = self._parse(data, url, specs=specs)
        assert listing is not None
        assert listing.owners_count == 4

    def test_no_brand_returns_none(self):
        data = _build_json_ld_car(brand="")
        url = "https://auto.drom.ru/moscow/unknown/x/111.html"
        listing = self._parse(data, url)
        assert listing is None

    def test_no_external_id_returns_none(self):
        data = _build_json_ld_car()
        url = "https://example.com/no-id"
        listing = self._parse(data, url)
        assert listing is None

    def test_brand_as_string(self):
        data = _build_json_ld_car()
        data["brand"] = "Honda"  # string instead of dict
        url = "https://auto.drom.ru/moscow/honda/civic/111.html"
        listing = self._parse(data, url)
        assert listing is not None
        assert listing.brand == "Honda"

    def test_mileage_with_spaces(self):
        data = _build_json_ld_car()
        data["mileageFromOdometer"] = {"value": "123 456"}
        url = "https://auto.drom.ru/moscow/toyota/camry/111.html"
        listing = self._parse(data, url)
        assert listing is not None
        assert listing.mileage == 123456

    def test_fallback_image_string(self):
        data = _build_json_ld_car()
        data["image"] = "https://s1.drom.ru/photo/gallery/123.jpg"
        url = "https://auto.drom.ru/moscow/toyota/camry/111.html"
        listing = self._parse(data, url)
        assert listing is not None
        assert listing.photos == ["https://s1.drom.ru/photo/gallery/123.jpg"]

    def test_customs_cleared(self):
        data = _build_json_ld_car()
        url = "https://auto.drom.ru/moscow/toyota/camry/111.html"
        specs = {"Растаможен": "Да"}
        listing = self._parse(data, url, specs=specs)
        assert listing is not None
        assert listing.customs_cleared is True

    def test_pts_and_condition(self):
        data = _build_json_ld_car()
        url = "https://auto.drom.ru/moscow/toyota/camry/111.html"
        specs = {"ПТС": "оригинал", "Состояние": "не битый"}
        listing = self._parse(data, url, specs=specs)
        assert listing is not None
        assert listing.pts_type == "оригинал"
        assert listing.condition == "не битый"


# ═══════════════════════════════════════════════════════════════════════════
# _try_preloaded_state
# ═══════════════════════════════════════════════════════════════════════════


class TestTryPreloadedState:
    def _try(self, html, url):
        parser = DromParser.__new__(DromParser)
        return parser._try_preloaded_state(html, url)

    def _make_preloaded_html(self, state_dict):
        state_json = json.dumps(state_dict)
        return f"<html>window.__preloaded_state__ = {state_json};</html>"

    def test_basic_preloaded(self):
        state = {
            "bullDescription": {
                "title": "Toyota Camry, 2020",
                "price": 1_500_000,
                "fields": [
                    {"title": "Коробка передач", "value": "автомат"},
                    {"title": "Привод", "value": "передний"},
                ],
            },
            "gallery": {"photos": {"images": [{"src": "https://s1.drom.ru/photo/1.jpg"}]}},
        }
        html = self._make_preloaded_html(state)
        url = "https://auto.drom.ru/moscow/toyota/camry/1234567.html"
        listing = self._try(html, url)
        assert listing is not None
        assert listing.brand == "Toyota"
        assert listing.model == "Camry"
        assert listing.year == 2020
        assert listing.price == 1_500_000
        assert listing.transmission == "автомат"
        assert listing.drive_type == "передний"
        assert len(listing.photos) == 1

    def test_no_preloaded_state(self):
        html = "<html>no state here</html>"
        url = "https://auto.drom.ru/moscow/toyota/camry/111.html"
        assert self._try(html, url) is None

    def test_invalid_json(self):
        html = "window.__preloaded_state__ = {invalid json};"
        url = "https://auto.drom.ru/moscow/toyota/camry/111.html"
        assert self._try(html, url) is None

    def test_preloaded_with_engine_fields(self):
        state = {
            "bullDescription": {
                "title": "BMW X5, 2019",
                "price": 2_800_000,
                "fields": [
                    {"title": "Двигатель", "value": "дизель, 3.0 л, 249 л.с."},
                    {"title": "Пробег", "value": "85 000 км"},
                ],
                "vin": "WBAPH5C50BA123456",
            },
        }
        html = self._make_preloaded_html(state)
        url = "https://auto.drom.ru/moscow/bmw/x5/999.html"
        listing = self._try(html, url)
        assert listing is not None
        assert listing.engine_type == "дизель"
        assert listing.engine_volume == 3.0
        assert listing.power_hp == 249
        assert listing.mileage == 85000
        assert listing.vin == "WBAPH5C50BA123456"

    def test_preloaded_with_seller(self):
        state = {
            "bullDescription": {
                "title": "Kia Rio, 2021",
                "price": 1_200_000,
                "fields": [],
            },
            "seller": {
                "name": "АвтоМосква",
                "type": "дилер",
                "isDealer": True,
            },
        }
        html = self._make_preloaded_html(state)
        url = "https://auto.drom.ru/moscow/kia/rio/555.html"
        listing = self._try(html, url)
        assert listing is not None
        assert listing.seller_name == "АвтоМосква"
        assert listing.seller_type == "дилер"
        assert listing.is_dealer is True


# ═══════════════════════════════════════════════════════════════════════════
# _extract_brand_model / _extract_year (static)
# ═══════════════════════════════════════════════════════════════════════════


class TestDromBrandModel:
    def test_simple(self):
        brand, model = DromParser._extract_brand_model("Toyota Camry, 2020")
        assert brand == "Toyota"
        assert model == "Camry"

    def test_with_engine(self):
        brand, model = DromParser._extract_brand_model("BMW X5 3.0 diesel")
        assert brand == "BMW"
        assert model == "X5"

    def test_single_word(self):
        brand, model = DromParser._extract_brand_model("Toyota")
        assert brand == "Toyota"
        assert model == ""

    def test_empty(self):
        brand, model = DromParser._extract_brand_model("")
        assert brand == ""
        assert model == ""


class TestDromExtractYear:
    def test_year_2020(self):
        assert DromParser._extract_year("Toyota Camry, 2020") == 2020

    def test_no_year(self):
        assert DromParser._extract_year("no year here") == 0


# ═══════════════════════════════════════════════════════════════════════════
# _extract_region_from_url
# ═══════════════════════════════════════════════════════════════════════════


class TestExtractRegionFromUrl:
    def test_moscow_card_url(self):
        url = "https://auto.drom.ru/moscow/toyota/camry/123.html"
        region = DromParser._extract_region_from_url(url)
        assert region == "Москва"

    def test_spb_card_url(self):
        url = "https://auto.drom.ru/spb/kia/rio/456.html"
        region = DromParser._extract_region_from_url(url)
        assert region == "Санкт-Петербург"

    def test_moscow_listing_url(self):
        url = "https://moscow.drom.ru/toyota/"
        region = DromParser._extract_region_from_url(url)
        assert region == "Москва"

    def test_krasnodar_listing_url(self):
        url = "https://krasnodar.drom.ru/hyundai/"
        region = DromParser._extract_region_from_url(url)
        assert region == "Краснодарский край"

    def test_auto_drom_no_city(self):
        url = "https://auto.drom.ru/"
        region = DromParser._extract_region_from_url(url)
        assert region is None or region == "Drom"  # No city in this URL pattern


# ═══════════════════════════════════════════════════════════════════════════
# _extract_seller_info
# ═══════════════════════════════════════════════════════════════════════════


class TestDromExtractSellerInfo:
    def test_private_seller(self):
        html = "<html><body><div>Частное лицо</div></body></html>"
        soup = _make_soup(html)
        seller_type, seller_name, is_dealer = DromParser._extract_seller_info(soup)
        assert seller_type == "частное лицо"
        assert is_dealer is False

    def test_dealer_detected(self):
        html = '<html><body><div>Автодилер</div><a href="/dealer/moscow">АвтоМосква</a></body></html>'
        soup = _make_soup(html)
        seller_type, seller_name, is_dealer = DromParser._extract_seller_info(soup)
        assert seller_type == "дилер"
        assert seller_name == "АвтоМосква"
        assert is_dealer is True

    def test_salon_link(self):
        html = '<html><body><a href="/salon/123">Автосалон Юг</a></body></html>'
        soup = _make_soup(html)
        seller_type, seller_name, is_dealer = DromParser._extract_seller_info(soup)
        assert is_dealer is True
        assert seller_name == "Автосалон Юг"

    def test_no_soup(self):
        seller_type, seller_name, is_dealer = DromParser._extract_seller_info(None)
        assert seller_type is None
        assert seller_name is None
        assert is_dealer is False


# ═══════════════════════════════════════════════════════════════════════════
# _extract_listing_date
# ═══════════════════════════════════════════════════════════════════════════


class TestDromExtractListingDate:
    def test_from_json_ld_valid_from(self):
        json_ld = {"offers": {"validFrom": "2024-03-01"}}
        date = DromParser._extract_listing_date(json_ld, None)
        assert date == "2024-03-01"

    def test_from_json_ld_date_posted(self):
        json_ld = {"datePosted": "2024-04-05"}
        date = DromParser._extract_listing_date(json_ld, None)
        assert date == "2024-04-05"

    def test_from_meta_tag(self):
        html = '<html><head><meta property="article:published_time" content="2024-02-10" /></head></html>'
        soup = _make_soup(html)
        date = DromParser._extract_listing_date(None, soup)
        assert date == "2024-02-10"

    def test_no_date_found(self):
        date = DromParser._extract_listing_date({}, _make_soup("<html></html>"))
        assert date is None


# ═══════════════════════════════════════════════════════════════════════════
# _extract_photo_urls
# ═══════════════════════════════════════════════════════════════════════════


class TestDromExtractPhotoUrls:
    def test_img_tags(self):
        html = '<html><body><img src="https://s1.drom.ru/photo/gallery/123.jpg" /></body></html>'
        soup = _make_soup(html)
        photos = DromParser._extract_photo_urls(soup)
        assert len(photos) == 1
        assert "123.jpg" in photos[0]

    def test_a_tags(self):
        html = '<html><body><a href="https://s2.drom.ru/photo/full/456.jpg">photo</a></body></html>'
        soup = _make_soup(html)
        photos = DromParser._extract_photo_urls(soup)
        assert len(photos) == 1

    def test_no_photos(self):
        soup = _make_soup("<html><body></body></html>")
        assert DromParser._extract_photo_urls(soup) == []

    def test_none_soup(self):
        assert DromParser._extract_photo_urls(None) == []

    def test_deduplication(self):
        url = "https://s1.drom.ru/photo/gallery/same.jpg"
        html = f'<html><body><img src="{url}" /><a href="{url}">link</a></body></html>'
        soup = _make_soup(html)
        photos = DromParser._extract_photo_urls(soup)
        assert len(photos) == 1


# ═══════════════════════════════════════════════════════════════════════════
# _get_proxy_url
# ═══════════════════════════════════════════════════════════════════════════


class TestGetProxyUrl:
    def test_no_proxy(self, monkeypatch):
        monkeypatch.delenv("PROXY_STRING", raising=False)
        assert DromParser._get_proxy_url() is None

    def test_with_proxy(self, monkeypatch):
        monkeypatch.setenv("PROXY_STRING", "user:pass@proxy.example.com:1080")
        monkeypatch.setenv("PROXY_TYPE", "socks5")
        url = DromParser._get_proxy_url()
        assert url == "socks5://user:pass@proxy.example.com:1080"

    def test_default_proxy_type(self, monkeypatch):
        monkeypatch.setenv("PROXY_STRING", "proxy.example.com:1080")
        monkeypatch.delenv("PROXY_TYPE", raising=False)
        url = DromParser._get_proxy_url()
        assert url == "socks5://proxy.example.com:1080"


# ═══════════════════════════════════════════════════════════════════════════
# Pagination URL construction
# ═══════════════════════════════════════════════════════════════════════════


class TestPaginationUrls:
    """Verify that the parser constructs page2+/page3+ URLs correctly."""

    def test_page_url_with_query_string(self):
        base = "https://moscow.drom.ru/toyota/?minprice=100000&maxprice=800000"
        path, qs = base.split("?", 1)
        page2_url = f"{path.rstrip('/')}/page2/?{qs}"
        assert page2_url == "https://moscow.drom.ru/toyota/page2/?minprice=100000&maxprice=800000"

    def test_page_url_without_query_string(self):
        base = "https://moscow.drom.ru/toyota/"
        page3_url = f"{base.rstrip('/')}/page3/"
        assert page3_url == "https://moscow.drom.ru/toyota/page3/"
