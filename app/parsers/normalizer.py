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

# Brand normalization — different sources use different names for the same brand.
# Keys MUST be lowercase. Lookup is case-insensitive (see normalize_brand).
_BRAND_MAP: dict[str, str] = {
    # ── LADA / ВАЗ ──────────────────────────────────────────────────────────
    "ваз": "LADA",
    "вaz": "LADA",
    "лада": "LADA",
    "vaz": "LADA",
    "lada": "LADA",
    "vaz_lada": "LADA",
    "vaz (lada)": "LADA",
    "lada (ваз)": "LADA",
    "lada (вaz)": "LADA",
    "лада (ваз)": "LADA",
    "ваз (lada)": "LADA",
    "lada(ваз)": "LADA",
    # ── UAZ / УАЗ ───────────────────────────────────────────────────────────
    "уаз": "UAZ",
    "uaz": "UAZ",
    # ── GAZ / ГАЗ ───────────────────────────────────────────────────────────
    "газ": "GAZ",
    "gaz": "GAZ",
    # ── ZAZ / ЗАЗ ───────────────────────────────────────────────────────────
    "заз": "ZAZ",
    "zaz": "ZAZ",
    # ── Moskvitch / Москвич ──────────────────────────────────────────────────
    "москвич": "Moskvitch",
    # ── Hyundai (many Russian transliterations) ─────────────────────────────
    "хёндэ": "Hyundai",
    "хендай": "Hyundai",
    "хендэ": "Hyundai",
    "хундай": "Hyundai",
    # ── Kia ──────────────────────────────────────────────────────────────────
    "киа": "Kia",
    "кия": "Kia",
    # ── Japanese ─────────────────────────────────────────────────────────────
    "тойота": "Toyota",
    "ниссан": "Nissan",
    "мазда": "Mazda",
    "митсубиси": "Mitsubishi",
    "мицубиси": "Mitsubishi",
    "субару": "Subaru",
    "сузуки": "Suzuki",
    "хонда": "Honda",
    "лексус": "Lexus",
    "инфинити": "Infiniti",
    "акура": "Acura",
    # ── German ───────────────────────────────────────────────────────────────
    "мерседес": "Mercedes-Benz",
    "мерседес-бенц": "Mercedes-Benz",
    "mercedes": "Mercedes-Benz",
    "mercedes-benz": "Mercedes-Benz",
    "бмв": "BMW",
    "bmw": "BMW",
    "фольксваген": "Volkswagen",
    "шкода": "Skoda",
    "skoda": "Skoda",
    "ауди": "Audi",
    "audi": "Audi",
    "опель": "Opel",
    "порше": "Porsche",
    "porsche": "Porsche",
    # ── Korean ───────────────────────────────────────────────────────────────
    "дэу": "Daewoo",
    "равон": "Ravon",
    # ── French ───────────────────────────────────────────────────────────────
    "рено": "Renault",
    "пежо": "Peugeot",
    "ситроен": "Citroen",
    # ── American ─────────────────────────────────────────────────────────────
    "форд": "Ford",
    "шевроле": "Chevrolet",
    "джип": "Jeep",
    "додж": "Dodge",
    "крайслер": "Chrysler",
    "кадиллак": "Cadillac",
    "линкольн": "Lincoln",
    # ── British ──────────────────────────────────────────────────────────────
    "лэнд ровер": "Land Rover",
    "ленд ровер": "Land Rover",
    "ягуар": "Jaguar",
    "бентли": "Bentley",
    "роллс-ройс": "Rolls-Royce",
    "мини": "MINI",
    # ── Swedish / Other European ────────────────────────────────────────────
    "вольво": "Volvo",
    # ── Italian ──────────────────────────────────────────────────────────────
    "феррари": "Ferrari",
    "ламборгини": "Lamborghini",
    # ── Chinese ──────────────────────────────────────────────────────────────
    "чери": "Chery",
    "черри": "Chery",
    "хавал": "Haval",
    "хавейл": "Haval",
    "джили": "Geely",
    "чанган": "Changan",
    "грейт волл": "Great Wall",
    "лифан": "Lifan",
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
    "nn": "Нижний Новгород",
    "nnovgorod": "Нижний Новгород",
    "nizhniynovgorod": "Нижний Новгород",
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
    """Normalize brand name to canonical form.

    Lookup order:
    1. Exact match (case-insensitive) in _BRAND_MAP
    2. Strip parenthetical suffix and retry — e.g. "Lada (ВАЗ)" → try "lada"
    3. If still not found, title-case the original brand
    """
    if not brand:
        return None
    stripped = brand.strip()
    key = stripped.lower()

    # 1. Exact match
    if key in _BRAND_MAP:
        return _BRAND_MAP[key]

    # 2. Strip parenthetical part: "Lada (ВАЗ)" → "lada", "LADA (ВАЗ)" → "lada"
    base = re.sub(r"\s*\(.*?\)\s*", "", key).strip()
    if base and base != key and base in _BRAND_MAP:
        return _BRAND_MAP[base]

    # 3. Return as-is (don't title-case — preserves "BMW", "FAW", "JAC", etc.)
    return stripped


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
    """Extract city name from Drom URL.

    Card URLs use the path: https://auto.drom.ru/CITY/brand/model/id.html
    Listing URLs use subdomain: https://CITY.drom.ru/brand/...
    """
    # 1. Card URLs: auto.drom.ru/CITY/...
    m_path = re.match(r"https?://auto\.drom\.ru/(\w+)/", url)
    if m_path:
        city_slug = m_path.group(1)
        return DROM_CITY_MAP.get(city_slug, city_slug.title())

    # 2. Listing URLs: CITY.drom.ru/...
    m_sub = re.match(r"https?://(\w+)\.drom\.ru/", url)
    if m_sub and m_sub.group(1) != "auto":
        subdomain = m_sub.group(1)
        return DROM_CITY_MAP.get(subdomain, subdomain.title())
    return None
