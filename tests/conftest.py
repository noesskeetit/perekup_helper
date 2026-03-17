import json

import pytest


@pytest.fixture
def mock_listing_html():
    """Mock Avito listing page HTML with data-marker attributes."""
    return """
    <html>
    <body>
    <div data-marker="catalog-serp">
        <div data-marker="item" itemtype="http://schema.org/Product">
            <a data-marker="item-title" href="/moskva/avtomobili/toyota_camry_2020_12345">
                Toyota Camry, 2020
            </a>
            <meta itemprop="price" content="1500000" />
            <span data-marker="item-price">1 500 000 ₽</span>
        </div>
        <div data-marker="item" itemtype="http://schema.org/Product">
            <a data-marker="item-title" href="/moskva/avtomobili/bmw_3_series_2019_67890">
                BMW 3 Series, 2019
            </a>
            <meta itemprop="price" content="2300000" />
            <span data-marker="item-price">2 300 000 ₽</span>
        </div>
        <div data-marker="item" itemtype="http://schema.org/Product">
            <a data-marker="item-title" href="/moskva/avtomobili/kia_rio_2021_11111">
                Kia Rio, 2021
            </a>
            <meta itemprop="price" content="950000" />
        </div>
    </div>
    <a data-marker="pagination-button/nextPage" href="?p=2">Следующая</a>
    </body>
    </html>
    """


@pytest.fixture
def mock_listing_html_json():
    """Mock Avito listing page with embedded JSON data."""
    items_data = {
        "catalog": {
            "items": [
                {
                    "id": 99001,
                    "title": "Honda Civic, 2022",
                    "urlPath": "/moskva/avtomobili/honda_civic_2022_99001",
                    "price": 1800000,
                },
                {
                    "id": 99002,
                    "title": "Hyundai Solaris, 2020",
                    "urlPath": "/moskva/avtomobili/hyundai_solaris_2020_99002",
                    "price": 1100000,
                },
            ]
        }
    }
    return f"""
    <html>
    <body>
    <script type="application/json">{json.dumps(items_data)}</script>
    </body>
    </html>
    """


@pytest.fixture
def mock_card_html():
    """Mock Avito car ad page HTML."""
    return """
    <html>
    <head>
    <script type="application/ld+json">
    {
        "@type": "Product",
        "name": "Toyota Camry, 2020",
        "description": "Отличное состояние, один владелец, полное ТО у дилера",
        "offers": {"price": "1500000"},
        "image": ["https://99.img.avito.st/image/1/abc123", "https://99.img.avito.st/image/1/def456"],
        "vehicleIdentificationNumber": "JTDBR40E600123456"
    }
    </script>
    <meta property="og:image" content="https://99.img.avito.st/image/1/abc123" />
    </head>
    <body>
    <h1 data-marker="item-view/title-info">Toyota Camry, 2020</h1>
    <span data-marker="item-view/item-price" content="1500000">1 500 000 ₽</span>

    <div data-marker="item-view/item-description">
        Отличное состояние, один владелец, полное ТО у дилера.
        Не бит, не крашен. Пробег родной.
    </div>

    <ul data-marker="item-view/item-params">
        <li>Марка: Toyota</li>
        <li>Модель: Camry</li>
        <li>Год выпуска: 2020</li>
        <li>Пробег: 45 000 км</li>
        <li>Тип двигателя: Бензин</li>
        <li>Объём двигателя: 2.5 л</li>
        <li>Мощность: 200 л.с.</li>
        <li>Коробка передач: Автомат</li>
        <li>Привод: Передний</li>
        <li>Тип кузова: Седан</li>
        <li>Цвет: Белый</li>
        <li>Руль: Левый</li>
    </ul>

    <div data-marker="seller-info/name">Алексей</div>
    <span data-marker="item-view/item-address">Москва, Центральный район</span>

    <script>var data = {"marketPrice": 1650000};</script>
    </body>
    </html>
    """


@pytest.fixture
def mock_card_html_embedded_json():
    """Mock Avito card page with embedded JSON state."""
    embedded = {
        "props": {
            "item": {
                "id": 12345,
                "title": "BMW 3 Series, 2019",
                "description": "Полный пакет М, спортивные сиденья",
                "price": 2300000,
                "location": {"name": "Санкт-Петербург"},
            },
            "params": [
                {"title": "Марка", "value": "BMW"},
                {"title": "Модель", "value": "3 Series"},
                {"title": "Год выпуска", "value": "2019"},
                {"title": "Пробег", "value": "67 000 км"},
                {"title": "Тип двигателя", "value": "Бензин"},
                {"title": "Объём двигателя", "value": "2.0 л"},
                {"title": "Мощность", "value": "184 л.с."},
                {"title": "Коробка передач", "value": "Автомат"},
                {"title": "Привод", "value": "Задний"},
                {"title": "Тип кузова", "value": "Седан"},
                {"title": "Цвет", "value": "Чёрный"},
                {"title": "Руль", "value": "Левый"},
            ],
        }
    }
    vin_data = {"vin": "WBAPH5C55BA123456"}
    return f"""
    <html>
    <body>
    <script type="application/json">{json.dumps(embedded)}</script>
    <script>var vinData = {json.dumps(vin_data)};</script>
    </body>
    </html>
    """
