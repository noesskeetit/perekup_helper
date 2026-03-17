"""Tests for card page parser."""

from src.avito_parser.card_parser import parse_card_page


class TestParseCardPage:
    def test_parse_json_ld_card(self, mock_card_html):
        data = parse_card_page(mock_card_html, "https://www.avito.ru/moskva/avtomobili/toyota_camry_2020_12345")

        assert data["title"] == "Toyota Camry, 2020"
        assert data["price"] == 1500000
        assert data["vin"] == "JTDBR40E600123456"
        assert "Отличное состояние" in data["description"]

    def test_html_params_parsing(self, mock_card_html):
        data = parse_card_page(mock_card_html, "https://www.avito.ru/test_12345")

        assert data.get("brand") == "Toyota"
        assert data.get("model") == "Camry"
        assert data.get("year") == 2020
        assert data.get("mileage_km") == 45000
        assert data.get("engine_type") == "Бензин"
        assert data.get("engine_volume") == 2.5
        assert data.get("engine_power_hp") == 200
        assert data.get("transmission") == "Автомат"
        assert data.get("drive_type") == "Передний"
        assert data.get("body_type") == "Седан"
        assert data.get("color") == "Белый"
        assert data.get("steering_wheel") == "Левый"

    def test_market_price_extraction(self, mock_card_html):
        data = parse_card_page(mock_card_html, "https://www.avito.ru/test_12345")
        assert data.get("market_price") == 1650000

    def test_photo_urls_extraction(self, mock_card_html):
        data = parse_card_page(mock_card_html, "https://www.avito.ru/test_12345")
        assert "photo_urls" in data
        # Should contain avito image URLs
        import json

        photos = json.loads(data["photo_urls"])
        assert len(photos) >= 1
        assert any("avito.st" in p for p in photos)

    def test_seller_and_location(self, mock_card_html):
        data = parse_card_page(mock_card_html, "https://www.avito.ru/test_12345")
        assert data.get("seller_name") == "Алексей"
        assert "Москва" in data.get("location", "")

    def test_external_id_from_url(self, mock_card_html):
        data = parse_card_page(mock_card_html, "https://www.avito.ru/moskva/avtomobili/toyota_camry_2020_12345")
        assert data["external_id"] == "12345"

    def test_parse_embedded_json_card(self, mock_card_html_embedded_json):
        data = parse_card_page(
            mock_card_html_embedded_json,
            "https://www.avito.ru/spb/avtomobili/bmw_3_2019_54321",
        )
        assert data.get("external_id") == "12345"
        assert data.get("brand") == "BMW"
        assert data.get("model") == "3 Series"
        assert data.get("year") == 2019
        assert data.get("mileage_km") == 67000

    def test_vin_from_script(self, mock_card_html_embedded_json):
        data = parse_card_page(mock_card_html_embedded_json, "https://www.avito.ru/test_12345")
        assert data.get("vin") == "WBAPH5C55BA123456"

    def test_empty_page(self):
        data = parse_card_page("<html><body></body></html>", "https://www.avito.ru/test_99999")
        assert data.get("external_id") == "99999"
        assert data.get("url") == "https://www.avito.ru/test_99999"
