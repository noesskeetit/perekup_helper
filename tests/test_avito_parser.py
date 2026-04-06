"""Tests for Avito parser (app/parsers/avito.py) and detail enrichment (app/parsers/avito_detail.py).

Tests cover:
- Brand/model extraction from titles
- Year extraction from titles
- Photo URL extraction from ad objects
- Ad-to-ParsedListing conversion
- Detail page parameter extraction (JSON blocks)
- Engine string parsing
- Mileage/integer parsing helpers
- Russian boolean parsing
- Description, photo, seller, location, date, and price-estimate extraction
- Unmapped attribute collection
- Edge cases: missing fields, malformed HTML, empty inputs
"""

from __future__ import annotations

from types import SimpleNamespace

from app.parsers.avito import (
    AvitoParser,
    _extract_brand_model,
    _extract_year,
    _first_photo_url,
)
from app.parsers.avito_detail import (
    _extract_description,
    _extract_listing_date,
    _extract_location,
    _extract_params_from_json,
    _extract_photos,
    _extract_price_estimate,
    _extract_seller_info,
    _parse_bool_russian,
    _parse_engine,
    _parse_int,
    enrich_listing_from_detail,
)
from app.parsers.base import ParsedListing

# ── Helpers ─────────────────────────────────────────────────────────────────


def _make_listing(**overrides) -> ParsedListing:
    defaults = {
        "source": "avito",
        "external_id": "avito-001",
        "brand": "Toyota",
        "model": "Camry",
        "year": 2020,
        "price": 1_500_000,
        "url": "https://www.avito.ru/moskva/avtomobili/toyota_camry_12345",
    }
    defaults.update(overrides)
    return ParsedListing(**defaults)


def _make_ad(**kwargs):
    """Create a SimpleNamespace mimicking an AviPars Item object."""
    defaults = {
        "id": 12345,
        "title": "Toyota Camry 2.5 AT, 2019, 45 000 km",
        "priceDetailed": SimpleNamespace(value=1_500_000),
        "urlPath": "/moskva/avtomobili/toyota_camry_12345",
        "description": "Good condition",
        "images": [],
        "location": SimpleNamespace(name="Москва"),
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ═══════════════════════════════════════════════════════════════════════════
# _extract_brand_model
# ═══════════════════════════════════════════════════════════════════════════


class TestExtractBrandModel:
    def test_simple_brand_model(self):
        brand, model = _extract_brand_model("Toyota Camry 2.5 AT, 2019, 45 000 km")
        assert brand == "Toyota"
        assert model == "Camry"

    def test_parenthetical_brand(self):
        brand, model = _extract_brand_model("LADA (ВАЗ) Granta 1.6 MT, 2020")
        assert brand == "LADA (ВАЗ)"
        assert model == "Granta"

    def test_single_word_title(self):
        brand, model = _extract_brand_model("Toyota")
        assert brand == "Toyota"
        assert model == ""

    def test_title_with_only_year(self):
        brand, model = _extract_brand_model("BMW X5, 2021")
        assert brand == "BMW"
        assert model == "X5"

    def test_empty_title(self):
        brand, model = _extract_brand_model("")
        assert brand == ""
        assert model == ""

    def test_title_without_engine_spec(self):
        brand, model = _extract_brand_model("Kia Rio, 2022")
        assert brand == "Kia"
        assert model == "Rio"

    def test_cvt_transmission(self):
        brand, model = _extract_brand_model("Nissan Qashqai 2.0 CVT, 2020")
        assert brand == "Nissan"
        assert model == "Qashqai"

    def test_amt_transmission(self):
        brand, model = _extract_brand_model("LADA (ВАЗ) Vesta 1.6 AMT, 2021")
        assert brand == "LADA (ВАЗ)"
        assert model == "Vesta"


# ═══════════════════════════════════════════════════════════════════════════
# _extract_year
# ═══════════════════════════════════════════════════════════════════════════


class TestExtractYear:
    def test_year_in_title(self):
        assert _extract_year("Toyota Camry 2.5 AT, 2019, 45 000 km") == 2019

    def test_year_2021(self):
        assert _extract_year("BMW X5, 2021") == 2021

    def test_no_year(self):
        assert _extract_year("BMW X5 без года") == 0

    def test_year_1999(self):
        assert _extract_year("ВАЗ 2107, 1999") == 1999

    def test_year_2000(self):
        assert _extract_year("Honda Civic, 2000") == 2000


# ═══════════════════════════════════════════════════════════════════════════
# _first_photo_url
# ═══════════════════════════════════════════════════════════════════════════


class TestFirstPhotoUrl:
    def test_no_images(self):
        ad = SimpleNamespace(images=[])
        assert _first_photo_url(ad) == []

    def test_no_images_attr(self):
        ad = SimpleNamespace()
        assert _first_photo_url(ad) == []

    def test_images_with_640x480(self):
        img = SimpleNamespace(
            root={"640x480": "https://img.avito.st/640.jpg", "208x156": "https://img.avito.st/208.jpg"}
        )
        ad = SimpleNamespace(images=[img])
        photos = _first_photo_url(ad)
        assert photos == ["https://img.avito.st/640.jpg"]

    def test_images_fallback_to_208x156(self):
        img = SimpleNamespace(root={"208x156": "https://img.avito.st/208.jpg"})
        ad = SimpleNamespace(images=[img])
        photos = _first_photo_url(ad)
        assert photos == ["https://img.avito.st/208.jpg"]

    def test_images_fallback_to_any(self):
        img = SimpleNamespace(root={"100x100": "https://img.avito.st/100.jpg"})
        ad = SimpleNamespace(images=[img])
        photos = _first_photo_url(ad)
        assert photos == ["https://img.avito.st/100.jpg"]

    def test_images_none_root(self):
        img = SimpleNamespace(root=None)
        ad = SimpleNamespace(images=[img])
        assert _first_photo_url(ad) == []

    def test_max_five_photos(self):
        imgs = [SimpleNamespace(root={"640x480": f"https://img.avito.st/{i}.jpg"}) for i in range(10)]
        ad = SimpleNamespace(images=imgs)
        photos = _first_photo_url(ad)
        assert len(photos) == 5


# ═══════════════════════════════════════════════════════════════════════════
# AvitoParser._convert_ad
# ═══════════════════════════════════════════════════════════════════════════


class TestConvertAd:
    def test_valid_ad_converted(self):
        ad = _make_ad()
        parser = AvitoParser.__new__(AvitoParser)
        listing = parser._convert_ad(ad)
        assert listing is not None
        assert listing.source == "avito"
        assert listing.external_id == "12345"
        assert listing.brand == "Toyota"
        assert listing.model == "Camry"
        assert listing.year == 2019
        assert listing.price == 1_500_000
        assert listing.url == "https://www.avito.ru/moskva/avtomobili/toyota_camry_12345"
        assert listing.city == "Москва"

    def test_missing_id_returns_none(self):
        ad = _make_ad(id=None)
        parser = AvitoParser.__new__(AvitoParser)
        assert parser._convert_ad(ad) is None

    def test_empty_title_returns_none(self):
        ad = _make_ad(title="")
        parser = AvitoParser.__new__(AvitoParser)
        assert parser._convert_ad(ad) is None

    def test_no_price_detailed(self):
        ad = _make_ad(priceDetailed=None)
        parser = AvitoParser.__new__(AvitoParser)
        listing = parser._convert_ad(ad)
        assert listing is not None
        assert listing.price == 0

    def test_no_location(self):
        ad = _make_ad(location=None)
        parser = AvitoParser.__new__(AvitoParser)
        listing = parser._convert_ad(ad)
        assert listing is not None
        assert listing.city is None

    def test_empty_url_path(self):
        ad = _make_ad(urlPath="")
        parser = AvitoParser.__new__(AvitoParser)
        listing = parser._convert_ad(ad)
        assert listing is not None
        assert listing.url == ""


# ═══════════════════════════════════════════════════════════════════════════
# avito_detail: _parse_int
# ═══════════════════════════════════════════════════════════════════════════


class TestParseInt:
    def test_simple_number(self):
        assert _parse_int("42") == 42

    def test_number_with_spaces(self):
        assert _parse_int("45 000 км") == 45000

    def test_number_with_non_digits(self):
        assert _parse_int("3 владельца") == 3

    def test_empty_string(self):
        assert _parse_int("") is None

    def test_no_digits(self):
        assert _parse_int("нет данных") is None


# ═══════════════════════════════════════════════════════════════════════════
# avito_detail: _parse_bool_russian
# ═══════════════════════════════════════════════════════════════════════════


class TestParseBoolRussian:
    def test_da(self):
        assert _parse_bool_russian("Да") is True

    def test_net(self):
        assert _parse_bool_russian("Нет") is False

    def test_rastamozhena(self):
        assert _parse_bool_russian("Растаможена") is True

    def test_ne_rastamozhen(self):
        assert _parse_bool_russian("Не растаможен") is False

    def test_unknown(self):
        assert _parse_bool_russian("какой-то текст") is None

    def test_case_insensitive(self):
        assert _parse_bool_russian("ДА") is True

    def test_whitespace_handling(self):
        assert _parse_bool_russian("  да  ") is True


# ═══════════════════════════════════════════════════════════════════════════
# avito_detail: _parse_engine
# ═══════════════════════════════════════════════════════════════════════════


class TestParseEngine:
    def test_full_engine_string(self):
        listing = _make_listing()
        _parse_engine(listing, "2.0 л / 150 л.с. / Бензин")
        assert listing.engine_volume == 2.0
        assert listing.power_hp == 150
        assert listing.engine_type == "Бензин"

    def test_volume_and_fuel_only(self):
        listing = _make_listing()
        _parse_engine(listing, "1.8 / Гибрид")
        assert listing.engine_volume == 1.8
        assert listing.engine_type == "Гибрид"
        assert listing.power_hp is None

    def test_does_not_override_existing(self):
        listing = _make_listing(engine_volume=3.0, power_hp=200, engine_type="дизель")
        _parse_engine(listing, "2.0 л / 150 л.с. / Бензин")
        assert listing.engine_volume == 3.0
        assert listing.power_hp == 200
        assert listing.engine_type == "дизель"

    def test_diesel(self):
        listing = _make_listing()
        _parse_engine(listing, "2.2 л / 175 л.с. / Дизель")
        assert listing.engine_volume == 2.2
        assert listing.power_hp == 175
        assert listing.engine_type == "Дизель"

    def test_comma_volume(self):
        listing = _make_listing()
        _parse_engine(listing, "1,6 л / 105 л.с. / Бензин")
        assert listing.engine_volume == 1.6


# ═══════════════════════════════════════════════════════════════════════════
# avito_detail: _extract_params_from_json
# ═══════════════════════════════════════════════════════════════════════════


class TestExtractParamsFromJson:
    def test_extracts_mileage(self):
        html = '{"attributeId":123,"title":"Пробег","description":"45 000 км"}'
        params = _extract_params_from_json(html)
        assert params.get("mileage_raw") == "45 000 км"

    def test_extracts_transmission(self):
        html = '{"attributeId":200,"title":"Коробка передач","description":"АКПП"}'
        params = _extract_params_from_json(html)
        assert params.get("transmission") == "АКПП"

    def test_extracts_color(self):
        html = '{"attributeId":300,"title":"Цвет кузова","description":"Белый"}'
        params = _extract_params_from_json(html)
        assert params.get("color") == "Белый"

    def test_extracts_vin(self):
        html = '{"attributeId":400,"title":"VIN","description":"JTDBR40E600123456"}'
        params = _extract_params_from_json(html)
        assert params.get("vin") == "JTDBR40E600123456"

    def test_extracts_multiple_params(self):
        html = (
            '{"attributeId":1,"title":"Пробег","description":"10 000 км"}'
            '{"attributeId":2,"title":"Коробка передач","description":"МКПП"}'
            '{"attributeId":3,"title":"Привод","description":"передний"}'
        )
        params = _extract_params_from_json(html)
        assert len(params) == 3
        assert params["mileage_raw"] == "10 000 км"
        assert params["transmission"] == "МКПП"
        assert params["drive_type"] == "передний"

    def test_escaped_quotes(self):
        html = r"\"attributeId\":100,\"title\":\"Пробег\",\"description\":\"50 000 км\""
        # After unescaping the wrapping braces won't be there, but the regex
        # should still find the block once braces are present.
        html_with_braces = "{" + html + "}"
        params = _extract_params_from_json(html_with_braces)
        assert params.get("mileage_raw") == "50 000 км"

    def test_empty_html(self):
        assert _extract_params_from_json("") == {}

    def test_no_matching_titles(self):
        html = '{"attributeId":999,"title":"Неизвестное","description":"значение"}'
        params = _extract_params_from_json(html)
        assert params == {}

    def test_descriptions_pattern(self):
        html = '"descriptions":["передний"],"title":"Привод"'
        params = _extract_params_from_json(html)
        assert params.get("drive_type") == "передний"

    def test_first_value_wins(self):
        html = (
            '{"attributeId":1,"title":"Цвет","description":"Белый"}'
            '{"attributeId":2,"title":"Цвет","description":"Чёрный"}'
        )
        params = _extract_params_from_json(html)
        assert params.get("color") == "Белый"

    def test_pts_type(self):
        html = '{"attributeId":500,"title":"ПТС","description":"Оригинал"}'
        params = _extract_params_from_json(html)
        assert params.get("pts_type") == "Оригинал"

    def test_owners_count(self):
        html = '{"attributeId":600,"title":"Владельцев по ПТС","description":"2"}'
        params = _extract_params_from_json(html)
        assert params.get("owners_raw") == "2"


# ═══════════════════════════════════════════════════════════════════════════
# avito_detail: _extract_description
# ═══════════════════════════════════════════════════════════════════════════


class TestExtractDescription:
    def test_extracts_long_description(self):
        text = "A" * 50
        html = f'"description":"{text}"'
        result = _extract_description(html)
        assert result == text

    def test_skips_short_description(self):
        html = '"description":"short"'
        result = _extract_description(html)
        assert result is None

    def test_no_description(self):
        html = "<html>no description here</html>"
        result = _extract_description(html)
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════
# avito_detail: _extract_photos
# ═══════════════════════════════════════════════════════════════════════════


class TestExtractPhotos:
    def test_extracts_1280x960(self):
        html = (
            '{"1280x960":"https://1.img.avito.st/image/1/big.jpg","640x480":"https://1.img.avito.st/image/1/small.jpg"}'
        )
        photos = _extract_photos(html)
        assert any("big.jpg" in p for p in photos)

    def test_extracts_avito_cdn_urls(self):
        html = '"https://1.img.avito.st/image/12345/photo.jpg"'
        photos = _extract_photos(html)
        assert len(photos) >= 1

    def test_skips_avatars(self):
        html = '"https://1.img.avito.st/image/12345/avatar.jpg"'
        photos = _extract_photos(html)
        # avatar URLs should be filtered out
        assert all("avatar" not in p for p in photos)

    def test_empty_html(self):
        assert _extract_photos("") == []

    def test_deduplicates(self):
        url = "https://1.img.avito.st/image/12345/photo.jpg"
        html = f'"{url}" "{url}"'
        photos = _extract_photos(html)
        assert photos.count(url) == 1


# ═══════════════════════════════════════════════════════════════════════════
# avito_detail: _extract_seller_info
# ═══════════════════════════════════════════════════════════════════════════


class TestExtractSellerInfo:
    def test_extracts_seller_name(self):
        html = '"sellerName":"Иван Петров"'
        info = _extract_seller_info(html)
        assert info is not None
        assert info["name"] == "Иван Петров"

    def test_extracts_private_type(self):
        html = '"sellerType":"private"'
        info = _extract_seller_info(html)
        assert info is not None
        assert info["type"] == "private"

    def test_extracts_dealer_type(self):
        html = '"sellerType":"dealer"'
        info = _extract_seller_info(html)
        assert info is not None
        assert info["type"] == "dealer"

    def test_shop_id_sets_dealer(self):
        html = '"shopId":12345'
        info = _extract_seller_info(html)
        assert info is not None
        assert info.get("is_dealer") is True

    def test_is_company_sets_dealer(self):
        html = '"isCompany":true'
        info = _extract_seller_info(html)
        assert info is not None
        assert info.get("is_dealer") is True

    def test_seller_id_extracted(self):
        html = '"sellerId":999888'
        info = _extract_seller_info(html)
        assert info is not None
        assert info["id"] == "999888"

    def test_returns_none_when_no_info(self):
        html = "<html>nothing here</html>"
        info = _extract_seller_info(html)
        assert info is None


# ═══════════════════════════════════════════════════════════════════════════
# avito_detail: _extract_location
# ═══════════════════════════════════════════════════════════════════════════


class TestExtractLocation:
    def test_location_name(self):
        html = '"location":{"name":"Москва","parentName":"Московская область"}'
        loc = _extract_location(html)
        assert loc is not None
        assert loc["city"] == "Москва"
        assert loc["region"] == "Московская область"

    def test_address_field(self):
        html = '"address":"Краснодар, ул. Ленина 10"'
        loc = _extract_location(html)
        assert loc is not None
        assert loc["city"] == "Краснодар"

    def test_empty_html(self):
        loc = _extract_location("<html></html>")
        assert loc is None


# ═══════════════════════════════════════════════════════════════════════════
# avito_detail: _extract_listing_date
# ═══════════════════════════════════════════════════════════════════════════


class TestExtractListingDate:
    def test_iso_published_at(self):
        html = '"publishedAt":"2024-03-15T10:30:00Z"'
        assert _extract_listing_date(html) == "2024-03-15T10:30:00Z"

    def test_epoch_sort_timestamp(self):
        # 2024-03-15 in epoch seconds
        html = '"sortTimeStamp":1710504600'
        result = _extract_listing_date(html)
        assert result is not None
        assert "2024-03-15" in result

    def test_epoch_millis(self):
        html = '"sortTimeStamp":1710504600000'
        result = _extract_listing_date(html)
        assert result is not None
        assert "2024-03-15" in result

    def test_no_date(self):
        assert _extract_listing_date("<html></html>") is None


# ═══════════════════════════════════════════════════════════════════════════
# avito_detail: _extract_price_estimate
# ═══════════════════════════════════════════════════════════════════════════


class TestExtractPriceEstimate:
    def test_market_price(self):
        html = '"marketPrice":{"value":1500000}'
        assert _extract_price_estimate(html) == 1_500_000

    def test_price_estimate(self):
        html = '"priceEstimate":2000000'
        assert _extract_price_estimate(html) == 2_000_000

    def test_out_of_range_low(self):
        html = '"priceEstimate":5000'
        assert _extract_price_estimate(html) is None

    def test_out_of_range_high(self):
        html = '"priceEstimate":60000000'
        assert _extract_price_estimate(html) is None

    def test_no_estimate(self):
        assert _extract_price_estimate("<html></html>") is None


# ═══════════════════════════════════════════════════════════════════════════
# avito_detail: enrich_listing_from_detail (integration)
# ═══════════════════════════════════════════════════════════════════════════


class TestEnrichListingFromDetail:
    def test_enriches_mileage(self):
        listing = _make_listing()
        html = '{"attributeId":1,"title":"Пробег","description":"45 000 км"}'
        enrich_listing_from_detail(listing, html)
        assert listing.mileage == 45000

    def test_enriches_transmission(self):
        listing = _make_listing()
        html = '{"attributeId":2,"title":"Коробка передач","description":"АКПП"}'
        enrich_listing_from_detail(listing, html)
        assert listing.transmission == "АКПП"

    def test_enriches_vin(self):
        listing = _make_listing()
        html = '{"attributeId":3,"title":"VIN","description":"JTDBR40E600123456"}'
        enrich_listing_from_detail(listing, html)
        assert listing.vin == "JTDBR40E600123456"

    def test_does_not_overwrite_existing(self):
        listing = _make_listing(mileage=30000)
        html = '{"attributeId":1,"title":"Пробег","description":"45 000 км"}'
        enrich_listing_from_detail(listing, html)
        assert listing.mileage == 30000  # Not overwritten

    def test_enriches_engine_compound(self):
        listing = _make_listing()
        html = '{"attributeId":4,"title":"Двигатель","description":"2.0 л / 150 л.с. / Бензин"}'
        enrich_listing_from_detail(listing, html)
        assert listing.engine_volume == 2.0
        assert listing.power_hp == 150
        assert listing.engine_type == "Бензин"

    def test_enriches_customs(self):
        listing = _make_listing()
        html = '{"attributeId":5,"title":"Растаможен","description":"Да"}'
        enrich_listing_from_detail(listing, html)
        assert listing.customs_cleared is True

    def test_enriches_pts_type(self):
        listing = _make_listing()
        html = '{"attributeId":6,"title":"ПТС","description":"Оригинал"}'
        enrich_listing_from_detail(listing, html)
        assert listing.pts_type == "Оригинал"

    def test_enriches_damage_into_raw_data(self):
        listing = _make_listing()
        html = '{"attributeId":7,"title":"Повреждения","description":"Царапина на бампере"}'
        enrich_listing_from_detail(listing, html)
        assert listing.raw_data.get("damage") == "Царапина на бампере"

    def test_empty_html_no_crash(self):
        listing = _make_listing()
        enrich_listing_from_detail(listing, "")
        # Should not raise, listing unchanged
        assert listing.source == "avito"

    def test_enriches_seller_info(self):
        listing = _make_listing()
        html = '"sellerName":"Автодилер Москва" "sellerType":"dealer" "shopId":12345'
        enrich_listing_from_detail(listing, html)
        assert listing.seller_name == "Автодилер Москва"
        assert listing.seller_type == "dealer"
        assert listing.is_dealer is True

    def test_enriches_location(self):
        listing = _make_listing()
        html = '"location":{"name":"Казань","parentName":"Республика Татарстан"}'
        enrich_listing_from_detail(listing, html)
        assert listing.city == "Казань"
        assert listing.region == "Республика Татарстан"

    def test_initializes_raw_data_if_none(self):
        listing = _make_listing(raw_data=None)
        html = '{"attributeId":7,"title":"Повреждения","description":"none"}'
        enrich_listing_from_detail(listing, html)
        assert listing.raw_data is not None
        assert isinstance(listing.raw_data, dict)
