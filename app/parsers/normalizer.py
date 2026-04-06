"""Central normalization module for car listing data across all sources.

Normalizes categorical values (transmission, drive, body type, etc.)
to consistent enum values. Maps source-specific raw fields to canonical forms.
"""

from __future__ import annotations

import re
from enum import Enum

from app.parsers.base import ParsedListing


# ── Canonical enums ──────────────────────────────────────────────────────────

class EngineType(str, Enum):
    PETROL = "бензин"
    DIESEL = "дизель"
    HYBRID = "гибрид"
    ELECTRIC = "электро"
    LPG = "газ"
    PETROL_LPG = "бензин+газ"


class Transmission(str, Enum):
    MANUAL = "МКПП"
    AUTOMATIC = "АКПП"
    CVT = "вариатор"
    ROBOT = "робот"


class DriveType(str, Enum):
    FWD = "передний"
    RWD = "задний"
    AWD = "полный"


class BodyType(str, Enum):
    SEDAN = "седан"
    HATCHBACK = "хэтчбек"
    LIFTBACK = "лифтбек"
    WAGON = "универсал"
    CROSSOVER = "кроссовер"
    SUV = "внедорожник"
    COUPE = "купе"
    CABRIO = "кабриолет"
    MINIVAN = "минивэн"
    VAN = "фургон"
    PICKUP = "пикап"


class SteeringWheel(str, Enum):
    LEFT = "левый"
    RIGHT = "правый"


class SellerType(str, Enum):
    PRIVATE = "частное лицо"
    DEALER = "дилер"
    SALON = "автосалон"


class PtsType(str, Enum):
    ORIGINAL = "оригинал"
    DUPLICATE = "дубликат"
    ELECTRONIC = "электронный"


class Condition(str, Enum):
    NOT_DAMAGED = "не битый"
    DAMAGED = "битый"
    EMERGENCY = "аварийный"
    NEEDS_REPAIR = "требует ремонта"


# ── Normalization maps ───────────────────────────────────────────────────────

_TRANSMISSION_MAP: dict[str, str] = {
    # Russian
    "автомат": Transmission.AUTOMATIC.value,
    "автоматическая": Transmission.AUTOMATIC.value,
    "акпп": Transmission.AUTOMATIC.value,
    "механика": Transmission.MANUAL.value,
    "механическая": Transmission.MANUAL.value,
    "мкпп": Transmission.MANUAL.value,
    "вариатор": Transmission.CVT.value,
    "робот": Transmission.ROBOT.value,
    "роботизированная": Transmission.ROBOT.value,
    "amt": Transmission.ROBOT.value,
    # Auto.ru English enums
    "automatic": Transmission.AUTOMATIC.value,
    "mechanical": Transmission.MANUAL.value,
    "robot": Transmission.ROBOT.value,
    "variator": Transmission.CVT.value,
    "auto": Transmission.AUTOMATIC.value,
    # From Avito titles
    "at": Transmission.AUTOMATIC.value,
    "mt": Transmission.MANUAL.value,
    "cvt": Transmission.CVT.value,
}

_DRIVE_MAP: dict[str, str] = {
    "передний": DriveType.FWD.value,
    "задний": DriveType.RWD.value,
    "полный": DriveType.AWD.value,
    "front_drive": DriveType.FWD.value,
    "rear_drive": DriveType.RWD.value,
    "all_wheel_drive": DriveType.AWD.value,
    "forward_control": DriveType.FWD.value,
    "4wd": DriveType.AWD.value,
    "awd": DriveType.AWD.value,
    "fwd": DriveType.FWD.value,
    "rwd": DriveType.RWD.value,
}

_BODY_MAP: dict[str, str] = {
    "седан": BodyType.SEDAN.value,
    "хэтчбек": BodyType.HATCHBACK.value,
    "хетчбек": BodyType.HATCHBACK.value,
    "лифтбек": BodyType.LIFTBACK.value,
    "универсал": BodyType.WAGON.value,
    "кроссовер": BodyType.CROSSOVER.value,
    "внедорожник": BodyType.SUV.value,
    "купе": BodyType.COUPE.value,
    "кабриолет": BodyType.CABRIO.value,
    "минивэн": BodyType.MINIVAN.value,
    "фургон": BodyType.VAN.value,
    "пикап": BodyType.PICKUP.value,
    "микроавтобус": BodyType.VAN.value,
    "компактвэн": BodyType.MINIVAN.value,
    # Auto.ru English
    "sedan": BodyType.SEDAN.value,
    "hatchback": BodyType.HATCHBACK.value,
    "hatchback_3_doors": BodyType.HATCHBACK.value,
    "hatchback_5_doors": BodyType.HATCHBACK.value,
    "liftback": BodyType.LIFTBACK.value,
    "wagon": BodyType.WAGON.value,
    "allroad": BodyType.CROSSOVER.value,
    "allroad_3_doors": BodyType.SUV.value,
    "allroad_5_doors": BodyType.CROSSOVER.value,
    "coupe": BodyType.COUPE.value,
    "cabrio": BodyType.CABRIO.value,
    "minivan": BodyType.MINIVAN.value,
    "van": BodyType.VAN.value,
    "pickup": BodyType.PICKUP.value,
}

_ENGINE_MAP: dict[str, str] = {
    "бензин": EngineType.PETROL.value,
    "бензиновый": EngineType.PETROL.value,
    "дизель": EngineType.DIESEL.value,
    "дизельный": EngineType.DIESEL.value,
    "гибрид": EngineType.HYBRID.value,
    "электро": EngineType.ELECTRIC.value,
    "электрический": EngineType.ELECTRIC.value,
    "газ": EngineType.LPG.value,
    "газовый": EngineType.LPG.value,
    # Auto.ru English
    "gasoline": EngineType.PETROL.value,
    "diesel": EngineType.DIESEL.value,
    "hybrid": EngineType.HYBRID.value,
    "electro": EngineType.ELECTRIC.value,
    "lpg": EngineType.LPG.value,
    "turbo": EngineType.PETROL.value,
}

_STEERING_MAP: dict[str, str] = {
    "левый": SteeringWheel.LEFT.value,
    "правый": SteeringWheel.RIGHT.value,
    "left": SteeringWheel.LEFT.value,
    "right": SteeringWheel.RIGHT.value,
    "lhd": SteeringWheel.LEFT.value,
    "rhd": SteeringWheel.RIGHT.value,
}

# Brand normalization — different sources use different names
_BRAND_MAP: dict[str, str] = {
    "вaz": "LADA",
    "ваз": "LADA",
    "лада": "LADA",
    "vaz": "LADA",
    "vaz_lada": "LADA",
    "vaz (lada)": "LADA",
    "лада (ваз)": "LADA",
    "ваз (lada)": "LADA",
    "мерседес": "Mercedes-Benz",
    "мерседес-бенц": "Mercedes-Benz",
    "бмв": "BMW",
    "фольксваген": "Volkswagen",
    "тойота": "Toyota",
    "хёндэ": "Hyundai",
    "хендай": "Hyundai",
    "хундай": "Hyundai",
    "киа": "Kia",
    "ниссан": "Nissan",
    "рено": "Renault",
    "форд": "Ford",
    "мазда": "Mazda",
    "шевроле": "Chevrolet",
    "шкода": "Skoda",
    "ауди": "Audi",
    "опель": "Opel",
    "пежо": "Peugeot",
    "ситроен": "Citroen",
    "субару": "Subaru",
    "сузуки": "Suzuki",
    "митсубиси": "Mitsubishi",
    "хонда": "Honda",
    "лексус": "Lexus",
    "инфинити": "Infiniti",
    "вольво": "Volvo",
    "джип": "Jeep",
    "лэнд ровер": "Land Rover",
}

# Drom subdomain → city name
DROM_CITY_MAP: dict[str, str] = {
    "moscow": "Москва",
    "spb": "Санкт-Петербург",
    "krasnodar": "Краснодар",
    "samara": "Самара",
    "ekaterinburg": "Екатеринбург",
    "novosibirsk": "Новосибирск",
    "kazan": "Казань",
    "rostov": "Ростов-на-Дону",
    "nnovgorod": "Нижний Новгород",
    "chelyabinsk": "Челябинск",
    "voronezh": "Воронеж",
    "volgograd": "Волгоград",
    "ufa": "Уфа",
    "perm": "Пермь",
    "krasnoyarsk": "Красноярск",
    "omsk": "Омск",
    "vladivostok": "Владивосток",
    "habarovsk": "Хабаровск",
    "irkutsk": "Иркутск",
    "tula": "Тула",
    "barnaul": "Барнаул",
    "tyumen": "Тюмень",
    "saratov": "Саратов",
    "tolyatti": "Тольятти",
    "izhevsk": "Ижевск",
}


# ── Normalization functions ──────────────────────────────────────────────────

def _norm(value: str | None, mapping: dict[str, str]) -> str | None:
    """Normalize a value using a case-insensitive mapping."""
    if not value:
        return None
    key = value.strip().lower()
    return mapping.get(key, value.strip())


def normalize_brand(brand: str | None) -> str | None:
    """Normalize brand name to canonical form."""
    if not brand:
        return None
    key = brand.strip().lower()
    # Remove parenthetical parts: "ВАЗ (LADA)" → "ваз (lada)"
    if key in _BRAND_MAP:
        return _BRAND_MAP[key]
    # Check partial matches
    for pattern, canonical in _BRAND_MAP.items():
        if pattern in key or key in pattern:
            return canonical
    # Title case fallback
    return brand.strip().title() if brand.islower() else brand.strip()


def normalize_listing(listing: ParsedListing) -> ParsedListing:
    """Normalize all categorical fields in a ParsedListing to canonical values."""
    listing.brand = normalize_brand(listing.brand) or listing.brand
    listing.transmission = _norm(listing.transmission, _TRANSMISSION_MAP)
    listing.drive_type = _norm(listing.drive_type, _DRIVE_MAP)
    listing.body_type = _norm(listing.body_type, _BODY_MAP)
    listing.engine_type = _norm(listing.engine_type, _ENGINE_MAP)
    listing.steering_wheel = _norm(listing.steering_wheel, _STEERING_MAP)

    # Derive photo_count
    listing.photo_count = len(listing.photos) if listing.photos else 0

    # Derive is_dealer from seller_type
    if listing.seller_type:
        st = listing.seller_type.lower()
        listing.is_dealer = any(x in st for x in ("дилер", "салон", "dealer", "salon"))

    return listing


def extract_city_from_drom_url(url: str) -> str | None:
    """Extract city name from Drom URL subdomain."""
    m = re.match(r"https?://(\w+)\.drom\.ru/", url)
    if m and m.group(1) != "auto":
        subdomain = m.group(1)
        return DROM_CITY_MAP.get(subdomain, subdomain.title())
    return None
