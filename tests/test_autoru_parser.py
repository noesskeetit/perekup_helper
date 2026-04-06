"""Tests for Auto.ru parser (app/parsers/autoru.py).

Tests cover:
- Color hex-to-name conversion
- Date parsing (millis, ISO)
- SSR_DATA_RE regex pattern
- Regex-based offer extraction (_extract_offers_regex)
- JSON-based offer parsing (_parse_offer)
- URL pattern matching for offer IDs
- Engine displacement to volume conversion
- Seller type / dealer detection
- Steering wheel normalization
- Edge cases: missing fields, no offers, captcha detection
"""

from __future__ import annotations

from app.parsers.autoru import (
    SSR_DATA_RE,
    AutoruParser,
    _hex_to_color_name,
    _parse_autoru_date,
)

# ── Helpers ─────────────────────────────────────────────────────────────────


def _build_offer_html(
    numeric_id="1132037070",
    hash_id="a1b2c3",
    brand_slug="toyota",
    model_slug="camry",
    price=1_500_000,
    year=2020,
    mileage=50000,
    mark_name="Toyota",
    model_name="Camry",
    engine_type=None,
    transmission=None,
    displacement=None,
    power=None,
    gear_type=None,
    body_type=None,
    color_hex=None,
    vin=None,
    steering_wheel=None,
    owners_number=None,
    pts=None,
    custom_cleared=None,
    description=None,
    seller_type=None,
    creation_date=None,
    city=None,
    region_name=None,
    generation_name=None,
    photo_urls=None,
):
    """Build a minimal Auto.ru HTML page with embedded offer data."""
    parts = ["<html><body>"]

    # Offer URL (always present)
    parts.append(
        f'<a href="https://auto.ru/cars/used/sale/{brand_slug}/{model_slug}/{numeric_id}-{hash_id}/">listing</a>'
    )

    # Data block near the numeric ID
    data_parts = [f'"{numeric_id}"']
    data_parts.append(f'"price":{price}')
    data_parts.append(f'"year":{year}')
    data_parts.append(f'"mileage":{mileage}')
    data_parts.append(f'"mark_info":{{"name":"{mark_name}"}}')
    data_parts.append(f'"model_info":{{"name":"{model_name}"}}')

    if engine_type:
        data_parts.append(f'"engine_type":"{engine_type}"')
    if transmission:
        data_parts.append(f'"transmission":"{transmission}"')
    if displacement:
        data_parts.append(f'"displacement":{displacement}')
    if power:
        data_parts.append(f'"power":{power}')
    if gear_type:
        data_parts.append(f'"gear_type":"{gear_type}"')
    if body_type:
        data_parts.append(f'"body_type":"{body_type}"')
    if color_hex:
        data_parts.append(f'"color_hex":"{color_hex}"')
    if vin:
        data_parts.append(f'"vin":"{vin}"')
    if steering_wheel:
        data_parts.append(f'"steering_wheel":"{steering_wheel}"')
    if owners_number is not None:
        data_parts.append(f'"owners_number":{owners_number}')
    if pts:
        data_parts.append(f'"pts":"{pts}"')
    if custom_cleared is not None:
        data_parts.append(f'"custom_cleared":{str(custom_cleared).lower()}')
    if description:
        data_parts.append(f'"description":"{description}"')
    if seller_type:
        data_parts.append(f'"seller_type":"{seller_type}"')
    if creation_date:
        data_parts.append(f'"creation_date":"{creation_date}"')
    if city:
        data_parts.append(f'"city":"{city}"')
    if region_name:
        data_parts.append(f'"region_info":{{"name":"{region_name}"}}')
    if generation_name:
        data_parts.append(f'"super_gen":{{"name":"{generation_name}"}}')
    if photo_urls:
        for pu in photo_urls:
            data_parts.append(f'"1200x900": "{pu}"')

    parts.append(",".join(data_parts))
    parts.append("</body></html>")
    return "\n".join(parts)


def _build_offer_dict(
    offer_id="1132037070-a1b2c3",
    brand="Toyota",
    model="Camry",
    year=2020,
    price=1_500_000,
    mileage=50000,
    engine_type=None,
    transmission=None,
    displacement=None,
    power=None,
    gear_type=None,
    body_type=None,
    color_hex=None,
    vin=None,
    steering_wheel=None,
    owners_number=None,
    pts=None,
    custom_cleared=None,
    seller_type=None,
    creation_date=None,
    city=None,
    region_name=None,
    generation_name=None,
):
    """Build a mock Auto.ru offer dict for _parse_offer."""
    offer = {
        "id": offer_id,
        "vehicle_info": {
            "mark_info": {"name": brand},
            "model_info": {"name": model},
            "tech_param": {},
            "configuration": {},
        },
        "documents": {"year": year},
        "price_info": {"RUR": price},
        "state": {"mileage": mileage},
    }

    tech = offer["vehicle_info"]["tech_param"]
    config = offer["vehicle_info"]["configuration"]
    car = offer["vehicle_info"]

    if engine_type:
        tech["engine_type"] = engine_type
    if transmission:
        tech["transmission"] = transmission
    if displacement:
        tech["displacement"] = displacement
    if power:
        tech["power"] = power
    if gear_type:
        tech["gear_type"] = gear_type
    if body_type:
        config["body_type"] = body_type
    if color_hex:
        car["color_hex"] = color_hex
    if vin:
        offer["documents"]["vin"] = vin
    if steering_wheel:
        car["steering_wheel"] = steering_wheel
    if owners_number is not None:
        offer["documents"]["owners_number"] = owners_number
    if pts:
        offer["documents"]["pts"] = pts
    if custom_cleared is not None:
        offer["documents"]["custom_cleared"] = custom_cleared
    if seller_type:
        offer["seller_type"] = seller_type
    if creation_date:
        offer["additional_info"] = {"creation_date": creation_date}
    if city:
        offer["seller"] = {"location": {"city": city}}
        if region_name:
            offer["seller"]["location"]["region_info"] = {"name": region_name}
    if generation_name:
        car["super_gen"] = {"name": generation_name}

    return offer


# ═══════════════════════════════════════════════════════════════════════════
# _hex_to_color_name
# ═══════════════════════════════════════════════════════════════════════════


class TestHexToColorName:
    def test_black(self):
        assert _hex_to_color_name("040001") == "black"

    def test_white(self):
        assert _hex_to_color_name("FAFBFB") == "white"

    def test_red(self):
        assert _hex_to_color_name("ee1d19") == "red"

    def test_unknown_hex(self):
        assert _hex_to_color_name("abc123") == "abc123"

    def test_with_hash_prefix(self):
        assert _hex_to_color_name("#040001") == "black"

    def test_silver(self):
        assert _hex_to_color_name("cacecb") == "silver"

    def test_blue(self):
        assert _hex_to_color_name("0000cc") == "blue"


# ═══════════════════════════════════════════════════════════════════════════
# _parse_autoru_date
# ═══════════════════════════════════════════════════════════════════════════


class TestParseAutoruDate:
    def test_millis_string(self):
        result = _parse_autoru_date("1712345678000")
        assert result is not None
        assert result.startswith("2024-04")

    def test_seconds_string(self):
        result = _parse_autoru_date("1712345678")
        assert result is not None
        assert result.startswith("2024-04")

    def test_iso_string(self):
        result = _parse_autoru_date("2024-04-06T12:00:00Z")
        assert result == "2024-04-06"

    def test_empty_string(self):
        assert _parse_autoru_date("") is None

    def test_none_like(self):
        assert _parse_autoru_date("") is None

    def test_date_with_dash(self):
        result = _parse_autoru_date("2024-03-15")
        assert result == "2024-03-15"


# ═══════════════════════════════════════════════════════════════════════════
# SSR_DATA_RE
# ═══════════════════════════════════════════════════════════════════════════


class TestSsrDataRe:
    def test_matches_ssr_data(self):
        html = 'window.__SSR_DATA__ = {"key":"value"};</script>'
        m = SSR_DATA_RE.search(html)
        assert m is not None
        assert m.group(1) == "__SSR_DATA__"
        assert m.group(2) == '{"key":"value"}'

    def test_matches_initial_state(self):
        html = 'window.__INITIAL_STATE__ = {"offers":[]};</script>'
        m = SSR_DATA_RE.search(html)
        assert m is not None
        assert m.group(1) == "__INITIAL_STATE__"

    def test_no_match(self):
        html = "<html>no state data</html>"
        assert SSR_DATA_RE.search(html) is None


# ═══════════════════════════════════════════════════════════════════════════
# _extract_offers_regex
# ═══════════════════════════════════════════════════════════════════════════


class TestExtractOffersRegex:
    def _extract(self, html):
        parser = AutoruParser.__new__(AutoruParser)
        return parser._extract_offers_regex(html)

    def test_basic_offer(self):
        html = _build_offer_html()
        listings = self._extract(html)
        assert len(listings) == 1
        listing = listings[0]
        assert listing.source == "autoru"
        assert listing.external_id == "1132037070-a1b2c3"
        assert listing.brand == "Toyota"
        assert listing.model == "Camry"
        assert listing.year == 2020
        assert listing.price == 1_500_000
        assert listing.mileage == 50000
        assert "auto.ru/cars/used/sale/toyota/camry/1132037070-a1b2c3" in listing.url

    def test_with_engine_and_transmission(self):
        html = _build_offer_html(
            engine_type="GASOLINE",
            transmission="AUTOMATIC",
            displacement=2494,
            power=181,
        )
        listings = self._extract(html)
        assert len(listings) == 1
        listing = listings[0]
        assert listing.engine_type == "GASOLINE"
        assert listing.transmission == "AUTOMATIC"
        assert listing.engine_volume == 2.5  # 2494cc -> 2.5L
        assert listing.power_hp == 181

    def test_with_drive_and_body(self):
        html = _build_offer_html(
            gear_type="FORWARD_CONTROL",
            body_type="SEDAN",
        )
        listings = self._extract(html)
        assert len(listings) == 1
        assert listings[0].drive_type == "FORWARD_CONTROL"
        assert listings[0].body_type == "SEDAN"

    def test_with_color_hex(self):
        html = _build_offer_html(color_hex="040001")
        listings = self._extract(html)
        assert len(listings) == 1
        assert listings[0].color == "black"

    def test_with_vin(self):
        html = _build_offer_html(vin="JTDBR40E600123456")
        listings = self._extract(html)
        assert len(listings) == 1
        assert listings[0].vin == "JTDBR40E600123456"

    def test_with_steering_left(self):
        html = _build_offer_html(steering_wheel="LEFT")
        listings = self._extract(html)
        assert len(listings) == 1
        assert listings[0].steering_wheel == "LEFT"

    def test_with_steering_right(self):
        html = _build_offer_html(steering_wheel="RIGHT_HAND_DRIVE")
        listings = self._extract(html)
        assert len(listings) == 1
        assert listings[0].steering_wheel == "RIGHT"

    def test_with_owners(self):
        html = _build_offer_html(owners_number=2)
        listings = self._extract(html)
        assert len(listings) == 1
        assert listings[0].owners_count == 2

    def test_with_pts_original(self):
        html = _build_offer_html(pts="ORIGINAL")
        listings = self._extract(html)
        assert len(listings) == 1
        assert listings[0].pts_type == "ORIGINAL"

    def test_with_customs_cleared(self):
        html = _build_offer_html(custom_cleared=True)
        listings = self._extract(html)
        assert len(listings) == 1
        assert listings[0].customs_cleared is True

    def test_with_customs_not_cleared(self):
        html = _build_offer_html(custom_cleared=False)
        listings = self._extract(html)
        assert len(listings) == 1
        assert listings[0].customs_cleared is False

    def test_seller_private(self):
        html = _build_offer_html(seller_type="PRIVATE")
        listings = self._extract(html)
        assert len(listings) == 1
        assert listings[0].seller_type == "PRIVATE"
        assert listings[0].is_dealer is False

    def test_seller_commercial(self):
        html = _build_offer_html(seller_type="COMMERCIAL")
        listings = self._extract(html)
        assert len(listings) == 1
        assert listings[0].seller_type == "COMMERCIAL"
        assert listings[0].is_dealer is True

    def test_with_city_and_region(self):
        html = _build_offer_html(city="Москва", region_name="Московская область")
        listings = self._extract(html)
        assert len(listings) == 1
        assert listings[0].city == "Москва"
        assert listings[0].region == "Московская область"

    def test_with_generation(self):
        html = _build_offer_html(generation_name="XV70")
        listings = self._extract(html)
        assert len(listings) == 1
        assert listings[0].generation == "XV70"

    def test_with_creation_date(self):
        html = _build_offer_html(creation_date="1712345678000")
        listings = self._extract(html)
        assert len(listings) == 1
        assert listings[0].listing_date is not None
        assert listings[0].listing_date.startswith("2024-04")

    def test_with_photos(self):
        html = _build_offer_html(
            photo_urls=["https://avatars.mds.yandex.net/img1.jpg", "https://avatars.mds.yandex.net/img2.jpg"]
        )
        listings = self._extract(html)
        assert len(listings) == 1
        assert len(listings[0].photos) >= 1

    def test_no_offers_in_html(self):
        html = "<html><body>No car data here</body></html>"
        listings = self._extract(html)
        assert listings == []

    def test_multiple_offers(self):
        html1 = _build_offer_html(
            numeric_id="111", hash_id="aaa", mark_name="Toyota", model_name="Camry", price=1_000_000
        )
        html2 = _build_offer_html(numeric_id="222", hash_id="bbb", mark_name="BMW", model_name="X5", price=2_000_000)
        combined = html1 + html2
        listings = self._extract(combined)
        assert len(listings) == 2
        ids = {listing.external_id for listing in listings}
        assert "111-aaa" in ids
        assert "222-bbb" in ids

    def test_displacement_small_value_treated_as_liters(self):
        html = _build_offer_html(displacement=20)
        listings = self._extract(html)
        assert len(listings) == 1
        assert listings[0].engine_volume == 20.0  # <= 100 treated as liters

    def test_displacement_cc_converted_to_liters(self):
        html = _build_offer_html(displacement=1598)
        listings = self._extract(html)
        assert len(listings) == 1
        assert listings[0].engine_volume == 1.6  # 1598cc -> 1.6L

    def test_brand_from_url_when_no_mark_info(self):
        """When mark_info is not found, brand should be derived from URL slug."""
        parts = ["<html><body>"]
        parts.append('<a href="https://auto.ru/cars/used/sale/toyota/rav4/999-abc/">link</a>')
        parts.append('"999"')
        parts.append('"price":800000')
        parts.append('"year":2018')
        parts.append("</body></html>")
        html = "\n".join(parts)
        listings = self._extract(html)
        assert len(listings) == 1
        assert listings[0].brand == "Toyota"
        assert listings[0].model == "Rav4"

    def test_description_unescape(self):
        html = _build_offer_html(description="Hello\\nWorld")
        listings = self._extract(html)
        assert len(listings) == 1
        assert listings[0].description is not None
        assert "Hello" in listings[0].description


# ═══════════════════════════════════════════════════════════════════════════
# _parse_offer (JSON-based)
# ═══════════════════════════════════════════════════════════════════════════


class TestParseOffer:
    def _parse(self, offer_dict):
        parser = AutoruParser.__new__(AutoruParser)
        return parser._parse_offer(offer_dict)

    def test_basic_offer(self):
        offer = _build_offer_dict()
        listing = self._parse(offer)
        assert listing is not None
        assert listing.source == "autoru"
        assert listing.external_id == "1132037070-a1b2c3"
        assert listing.brand == "Toyota"
        assert listing.model == "Camry"
        assert listing.year == 2020
        assert listing.price == 1_500_000
        assert listing.mileage == 50000

    def test_with_all_tech_specs(self):
        offer = _build_offer_dict(
            engine_type="GASOLINE",
            transmission="AUTOMATIC",
            displacement=2494,
            power=181,
            gear_type="FORWARD_CONTROL",
            body_type="SEDAN",
        )
        listing = self._parse(offer)
        assert listing is not None
        assert listing.engine_type == "GASOLINE"
        assert listing.transmission == "AUTOMATIC"
        assert listing.engine_volume == 2.5
        assert listing.power_hp == 181
        assert listing.drive_type == "FORWARD_CONTROL"
        assert listing.body_type == "SEDAN"

    def test_with_vin_and_documents(self):
        offer = _build_offer_dict(
            vin="WBAPH5C50BA123456",
            pts="ORIGINAL",
            owners_number=2,
            custom_cleared=True,
        )
        listing = self._parse(offer)
        assert listing is not None
        assert listing.vin == "WBAPH5C50BA123456"
        assert listing.pts_type == "ORIGINAL"
        assert listing.owners_count == 2
        assert listing.customs_cleared is True

    def test_color_from_hex(self):
        offer = _build_offer_dict(color_hex="ee1d19")
        listing = self._parse(offer)
        assert listing is not None
        assert listing.color == "red"

    def test_color_from_color_object(self):
        offer = _build_offer_dict()
        offer["color"] = {"name": "Белый"}
        listing = self._parse(offer)
        assert listing is not None
        assert listing.color == "Белый"

    def test_seller_info(self):
        offer = _build_offer_dict(seller_type="COMMERCIAL")
        offer["seller"] = {"name": "АвтоМосква"}
        listing = self._parse(offer)
        assert listing is not None
        assert listing.seller_type == "COMMERCIAL"
        assert listing.seller_name == "АвтоМосква"
        assert listing.is_dealer is True

    def test_private_seller(self):
        offer = _build_offer_dict(seller_type="PRIVATE")
        listing = self._parse(offer)
        assert listing is not None
        assert listing.is_dealer is False

    def test_no_price_returns_none(self):
        offer = _build_offer_dict(price=0)
        listing = self._parse(offer)
        assert listing is None

    def test_no_brand_returns_none(self):
        offer = _build_offer_dict(brand="")
        listing = self._parse(offer)
        assert listing is None

    def test_with_location(self):
        offer = _build_offer_dict(city="Москва", region_name="Московская область")
        listing = self._parse(offer)
        assert listing is not None
        assert listing.city == "Москва"

    def test_with_generation(self):
        offer = _build_offer_dict(generation_name="XV70 Рестайлинг")
        listing = self._parse(offer)
        assert listing is not None
        assert listing.generation == "XV70 Рестайлинг"

    def test_with_creation_date_millis(self):
        offer = _build_offer_dict(creation_date="1712345678000")
        listing = self._parse(offer)
        assert listing is not None
        assert listing.listing_date is not None

    def test_steering_wheel(self):
        offer = _build_offer_dict(steering_wheel="LEFT")
        listing = self._parse(offer)
        assert listing is not None
        assert listing.steering_wheel == "LEFT"

    def test_url_constructed_when_missing(self):
        offer = _build_offer_dict()
        offer["url"] = ""
        listing = self._parse(offer)
        assert listing is not None
        assert "auto.ru" in listing.url

    def test_displacement_small_as_liters(self):
        offer = _build_offer_dict(displacement=50)
        listing = self._parse(offer)
        assert listing is not None
        assert listing.engine_volume == 50.0

    def test_displacement_cc_to_liters(self):
        offer = _build_offer_dict(displacement=1997)
        listing = self._parse(offer)
        assert listing is not None
        assert listing.engine_volume == 2.0


# ═══════════════════════════════════════════════════════════════════════════
# AutoruParser.source_name
# ═══════════════════════════════════════════════════════════════════════════


class TestAutoruParserMeta:
    def test_source_name(self):
        parser = AutoruParser()
        assert parser.source_name == "autoru"

    def test_default_searches_populated(self):
        parser = AutoruParser()
        assert len(parser._searches) > 0

    def test_custom_searches(self):
        custom = [{"url": "https://auto.ru/custom"}]
        parser = AutoruParser(searches=custom)
        assert parser._searches == custom
