"""Tests for auto.ru card page parser."""

import json

import pytest

from autoru_parser.card_parser import parse_card_page


@pytest.fixture
def mock_autoru_card_html():
    """Mock auto.ru card page with JSON-LD and embedded state."""
    return """<!DOCTYPE html>
<html>
<head>
<script type="application/ld+json">
{
    "@type": "Vehicle",
    "name": "Toyota Camry, 2020",
    "description": "Отличное состояние, один владелец",
    "vehicleIdentificationNumber": "JTDBR40E600123456",
    "offers": {"price": "1500000"},
    "image": ["https://avatars.mds.yandex.net/get-autoru-orig/123/abc/1200x900"]
}
</script>
<script id="initial-state" type="application/json">
{
    "card": {
        "offer": {
            "id": "1234567890-aabbccdd",
            "saleId": "1234567890-aabbccdd",
            "vehicle_info": {
                "mark": {"name": "Toyota"},
                "model": {"name": "Camry"},
                "tech_param": {
                    "year": 2020,
                    "engine_volume": 2500,
                    "engine_power": 200,
                    "transmission": "AUTOMATIC",
                    "drive": "FRONT"
                },
                "body_type": "SEDAN",
                "color": {"name": "Белый"}
            },
            "price_info": {"price": 1500000},
            "state": {"mileage": 45000},
            "description": "Отличное состояние, один владелец",
            "seller": {
                "name": "Алексей",
                "location": {"city": "Москва"}
            },
            "documents": {"vin": "JTDBR40E600123456"}
        }
    }
}
</script>
</head>
<body>
<h1 class="CardTitle">Toyota Camry, 2020</h1>
<script>var config = {"market_price": 1650000};</script>
</body>
</html>"""


@pytest.fixture
def mock_autoru_card_html_minimal():
    """Mock auto.ru card with minimal HTML, no JSON-LD."""
    return """<!DOCTYPE html>
<html>
<head>
<script id="initial-state" type="application/json">
{
    "card": {
        "offer": {
            "id": "9876543210-11223344",
            "saleId": "9876543210-11223344",
            "vehicle_info": {
                "mark": {"name": "BMW"},
                "model": {"name": "3 Series"},
                "tech_param": {"year": 2019}
            },
            "price_info": {"price": 2300000},
            "state": {"mileage": 67000},
            "documents": {"vin": "WBAPH5C55BA123456"}
        }
    }
}
</script>
</head>
<body>
<h1>BMW 3 Series, 2019</h1>
</body>
</html>"""


class TestParseCardPage:
    def test_json_ld_extraction(self, mock_autoru_card_html):
        data = parse_card_page(mock_autoru_card_html, "https://auto.ru/cars/used/sale/toyota/camry/1234567890-aabbccdd/")
        assert data["title"] == "Toyota Camry, 2020"
        assert data["price"] == 1500000
        assert data["vin"] == "JTDBR40E600123456"
        assert "Отличное состояние" in data["description"]

    def test_embedded_state_params(self, mock_autoru_card_html):
        data = parse_card_page(mock_autoru_card_html, "https://auto.ru/cars/used/sale/toyota/camry/1234567890-aabbccdd/")
        assert data.get("brand") == "Toyota"
        assert data.get("model") == "Camry"
        assert data.get("year") == 2020
        assert data.get("mileage_km") == 45000
        assert data.get("engine_power_hp") == 200
        assert data.get("transmission") == "AUTOMATIC"
        assert data.get("drive_type") == "FRONT"
        assert data.get("body_type") == "SEDAN"
        assert data.get("color") == "Белый"

    def test_seller_and_location(self, mock_autoru_card_html):
        data = parse_card_page(mock_autoru_card_html, "https://auto.ru/cars/used/sale/toyota/camry/1234567890-aabbccdd/")
        assert data.get("seller_name") == "Алексей"
        assert data.get("location") == "Москва"

    def test_external_id_from_embedded_state(self, mock_autoru_card_html):
        data = parse_card_page(mock_autoru_card_html, "https://auto.ru/cars/used/sale/toyota/camry/1234567890-aabbccdd/")
        assert data.get("external_id") == "1234567890-aabbccdd"

    def test_market_price_extraction(self, mock_autoru_card_html):
        data = parse_card_page(mock_autoru_card_html, "https://auto.ru/cars/used/sale/toyota/camry/1234567890-aabbccdd/")
        assert data.get("market_price") == 1650000

    def test_photo_urls_extraction(self, mock_autoru_card_html):
        data = parse_card_page(mock_autoru_card_html, "https://auto.ru/test/1234567890-aabbccdd/")
        assert "photo_urls" in data
        photos = json.loads(data["photo_urls"])
        assert len(photos) >= 1
        assert any("yandex.net" in p for p in photos)

    def test_minimal_card(self, mock_autoru_card_html_minimal):
        data = parse_card_page(mock_autoru_card_html_minimal, "https://auto.ru/cars/used/sale/bmw/3/9876543210-11223344/")
        assert data.get("brand") == "BMW"
        assert data.get("model") == "3 Series"
        assert data.get("year") == 2019
        assert data.get("price") == 2300000
        assert data.get("mileage_km") == 67000
        assert data.get("vin") == "WBAPH5C55BA123456"

    def test_external_id_from_url(self, mock_autoru_card_html_minimal):
        data = parse_card_page(mock_autoru_card_html_minimal, "https://auto.ru/cars/used/sale/bmw/3/9876543210-11223344/")
        assert data.get("external_id") == "9876543210-11223344"

    def test_empty_page(self):
        data = parse_card_page("<html><body></body></html>", "https://auto.ru/cars/used/sale/test/1234567890-aabbccdd/")
        assert data.get("external_id") == "1234567890-aabbccdd"
        assert data.get("url") == "https://auto.ru/cars/used/sale/test/1234567890-aabbccdd/"
