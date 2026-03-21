"""Seed data generator for PerekupHelper.

Populates the database with realistic test car listings and analysis data.
Idempotent: safe to run multiple times (clears existing seed data first).

Usage:
    python -m scripts.seed
"""

from __future__ import annotations

import asyncio
import random
import uuid
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.listing import AnalysisCategory, Listing, ListingAnalysis

# ---------------------------------------------------------------------------
# Car catalogue — brand -> [(model, min_price, max_price)]
# ---------------------------------------------------------------------------
CAR_CATALOGUE: dict[str, list[tuple[str, int, int]]] = {
    "Lada": [
        ("Vesta", 800_000, 1_500_000),
        ("Granta", 500_000, 1_000_000),
        ("Niva", 900_000, 1_800_000),
        ("XRAY", 700_000, 1_300_000),
    ],
    "Toyota": [
        ("Camry", 1_200_000, 3_000_000),
        ("RAV4", 1_500_000, 3_500_000),
        ("Corolla", 900_000, 2_200_000),
        ("Land Cruiser", 3_000_000, 7_000_000),
    ],
    "BMW": [
        ("3 Series", 1_500_000, 3_500_000),
        ("X5", 2_500_000, 6_000_000),
        ("5 Series", 2_000_000, 5_000_000),
        ("X3", 2_000_000, 4_500_000),
    ],
    "Kia": [
        ("K5", 1_500_000, 2_800_000),
        ("Sportage", 1_300_000, 3_000_000),
        ("Rio", 700_000, 1_500_000),
        ("Ceed", 900_000, 2_000_000),
    ],
    "Hyundai": [
        ("Tucson", 1_400_000, 3_200_000),
        ("Solaris", 600_000, 1_300_000),
        ("Creta", 1_000_000, 2_500_000),
        ("Santa Fe", 2_000_000, 4_000_000),
    ],
}

# ---------------------------------------------------------------------------
# Russian description templates
# ---------------------------------------------------------------------------
_DESCRIPTIONS: list[str] = [
    "Автомобиль в отличном состоянии, один владелец по ПТС. Вложений не требует. Все ТО пройдены у официального дилера.",
    "Продаю свой автомобиль, куплен у дилера. Обслуживание только у ОД. Комплект зимней резины в подарок.",
    "Машина в хорошем состоянии, пробег реальный. Два комплекта ключей. Салон без прожогов и потёртостей.",
    "Продам авто в связи с переездом. Не бит, не крашен. Стояло в гараже, эксплуатация бережная.",
    "Техническое состояние идеальное. Двигатель и коробка работают без нареканий. Кузов без ржавчины.",
    "Срочная продажа! Небольшой торг при осмотре. Есть царапина на бампере, остальное в отличном состоянии.",
    "Автомобиль после ДТП, восстановлен качественно. Все элементы заменены на оригинальные. Подушки не стреляли.",
    "Машина с пробегом, но в хорошем состоянии. Масло не ест, подвеска тихая. Готова к эксплуатации.",
    "Перекупам не звонить. Продаю только лично. Возможен обмен на более дорогую с моей доплатой.",
    "Авто обслуживалось только на фирменном сервисе. Есть все документы о проведённых работах. Кузов родной.",
    "Состояние нового автомобиля. Пробег минимальный, использовалась только по выходным. Гаражное хранение.",
    "Продаю из-за нехватки места на парковке. Машина полностью исправна. Готова к осмотру в любое время.",
    "Комплектация максимальная: кожаный салон, подогрев всех сидений, камера заднего вида, парктроники.",
    "Были мелкие косметические ремонты, подробности расскажу лично. На ходу — отлично, всё работает.",
    "Документы чистые, без обременений и залогов. Проверка по любым базам. Один собственник.",
    "Автомобиль с дубликатом ПТС. Причина — утеря оригинала. Юридически чистый, проверяйте.",
    "Внимание! Проблемы с документами — ограничения ФССП. Цена ниже рынка именно по этой причине.",
    "Двигатель после капремонта, пробег после ремонта 15 000 км. Работает ровно, расход в норме.",
    "Автомобиль в кредите, остаток 300 000. Возможно погашение при сделке. Торг уместен.",
    "Полная предпродажная подготовка. Свежее масло, новые тормозные колодки, заменены все фильтры.",
]

# ---------------------------------------------------------------------------
# AI analysis templates per category
# ---------------------------------------------------------------------------
_AI_SUMMARIES: dict[str, list[str]] = {
    AnalysisCategory.CLEAN.value: [
        "Чистый автомобиль, без юридических проблем. Один владелец, сервисная книжка.",
        "Хорошее состояние, все ТО пройдены вовремя. Кузов без повреждений.",
        "Автомобиль полностью исправен, документы в порядке. Рекомендуется к покупке.",
    ],
    AnalysisCategory.DAMAGED_BODY.value: [
        "Обнаружены следы кузовного ремонта. Возможно участие в ДТП.",
        "Элементы кузова имеют неравномерную толщину ЛКП. Капот и крыло перекрашены.",
        "Видны следы восстановления после аварии. Геометрия кузова нарушена.",
    ],
    AnalysisCategory.BAD_DOCS.value: [
        "Дубликат ПТС. Рекомендуется дополнительная проверка истории автомобиля.",
        "Обнаружены расхождения в документах. VIN в ПТС и на кузове отличаются.",
        "Автомобиль снят с учёта. Требуется проверка причин и юридической чистоты.",
    ],
    AnalysisCategory.DEBTOR.value: [
        "Обнаружены ограничения ФССП. Автомобиль может быть под арестом.",
        "Владелец имеет задолженности. Рекомендуется проверка на наличие залога.",
        "Автомобиль в залоге у банка. Покупка без погашения кредита невозможна.",
    ],
    AnalysisCategory.COMPLEX_BUT_PROFITABLE.value: [
        "Есть нюансы с документами, но цена значительно ниже рынка. Потенциально выгодная сделка.",
        "Требуется вложение в кузовной ремонт, но итоговая стоимость будет ниже рыночной.",
        "Сложный случай: дубликат ПТС + мелкий кузовной ремонт, но маржа покрывает риски.",
    ],
}

_FLAGS_MAP: dict[str, list[list[str]]] = {
    AnalysisCategory.CLEAN.value: [[], [], ["один владелец"]],
    AnalysisCategory.DAMAGED_BODY.value: [
        ["после ДТП"],
        ["перекрашен"],
        ["кузовной ремонт", "неровный ЛКП"],
    ],
    AnalysisCategory.BAD_DOCS.value: [
        ["дубликат ПТС"],
        ["расхождение VIN"],
        ["снят с учёта"],
    ],
    AnalysisCategory.DEBTOR.value: [
        ["ограничения ФССП"],
        ["залог"],
        ["кредит не погашен"],
    ],
    AnalysisCategory.COMPLEX_BUT_PROFITABLE.value: [
        ["дубликат ПТС", "низкая цена"],
        ["кузовной ремонт", "выгодная маржа"],
        ["сложная история", "высокая маржа"],
    ],
}

SEED_COUNT = 70
SEED_MARKER = "seed-data"


def _generate_vin() -> str:
    """Generate a pseudo-random 17-character VIN."""
    chars = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"
    return "".join(random.choice(chars) for _ in range(17))


def _build_listings(rng: random.Random) -> list[Listing]:
    """Build a list of seed Listing objects."""
    brands = list(CAR_CATALOGUE.keys())
    sources = ["avito", "autoru"]
    listings: list[Listing] = []

    for i in range(SEED_COUNT):
        brand = brands[i % len(brands)]
        model_name, min_price, max_price = rng.choice(CAR_CATALOGUE[brand])
        year = rng.randint(2005, 2024)
        mileage = rng.randint(1_000, 250_000)
        market_price = rng.randint(min_price, max_price)
        source = sources[i % 2]
        vin = _generate_vin()

        # ~30% are "below market" deals
        diff_pct = rng.uniform(-30.0, -5.0) if i < 20 else rng.uniform(-3.0, 15.0)

        price = int(market_price * (1 + diff_pct / 100))
        description = rng.choice(_DESCRIPTIONS)
        photos = [f"https://img.example.com/cars/{source}/{i + 1}/photo_{j}.jpg" for j in range(1, rng.randint(3, 8))]

        listing = Listing(
            id=uuid.uuid5(uuid.NAMESPACE_DNS, f"{SEED_MARKER}-{i}"),
            source=source,
            external_id=f"seed-{i:04d}",
            brand=brand,
            model=model_name,
            year=year,
            mileage=mileage,
            price=price,
            market_price=market_price,
            price_diff_pct=Decimal(str(round(diff_pct, 2))),
            description=description,
            url=f"https://{source}.ru/cars/seed-{i:04d}",
            photos=photos,
            raw_data={"seed": True, "index": i},
            vin=vin,
            is_duplicate=False,
            canonical_id=None,
        )
        listings.append(listing)

    # Mark some as duplicates (same VIN as another listing)
    duplicate_count = rng.randint(5, 10)
    for d in range(duplicate_count):
        original_idx = rng.randint(0, SEED_COUNT - duplicate_count - 1)
        dup_idx = SEED_COUNT - duplicate_count + d
        if dup_idx < len(listings):
            listings[dup_idx].vin = listings[original_idx].vin
            listings[dup_idx].is_duplicate = True
            listings[dup_idx].canonical_id = listings[original_idx].id

    return listings


def _build_analyses(listings: list[Listing], rng: random.Random) -> list[ListingAnalysis]:
    """Build a ListingAnalysis for every listing, covering all categories."""
    categories = list(AnalysisCategory)
    analyses: list[ListingAnalysis] = []

    for i, listing in enumerate(listings):
        # Distribute categories to ensure all are represented
        cat = categories[i % len(categories)]
        summaries = _AI_SUMMARIES[cat.value]
        flags_options = _FLAGS_MAP[cat.value]

        analysis = ListingAnalysis(
            id=uuid.uuid5(uuid.NAMESPACE_DNS, f"{SEED_MARKER}-analysis-{i}"),
            listing_id=listing.id,
            category=cat,
            confidence=Decimal(str(round(rng.uniform(0.6, 0.99), 3))),
            ai_summary=rng.choice(summaries),
            flags=rng.choice(flags_options) or None,
            score=round(rng.uniform(1.0, 10.0), 1),
        )
        analyses.append(analysis)

    return analyses


async def generate_seed_data(session: AsyncSession) -> int:
    """Generate and insert seed data. Returns number of listings created.

    Idempotent: deletes existing seed data (identified by external_id prefix)
    before inserting.
    """
    # Delete existing seed data (analyses cascade via FK)
    existing = await session.execute(select(Listing.id).where(Listing.external_id.like("seed-%")))
    existing_ids = [row[0] for row in existing.all()]
    if existing_ids:
        await session.execute(delete(ListingAnalysis).where(ListingAnalysis.listing_id.in_(existing_ids)))
        await session.execute(delete(Listing).where(Listing.id.in_(existing_ids)))
        await session.flush()

    # Use a fixed seed for reproducibility
    rng = random.Random(42)

    listings = _build_listings(rng)
    analyses = _build_analyses(listings, rng)

    session.add_all(listings)
    await session.flush()
    session.add_all(analyses)
    await session.commit()

    return len(listings)


async def main() -> None:
    """CLI entrypoint: connect to the configured database and seed it."""
    from app.db.session import async_session_factory
    from app.models.base import Base  # noqa: F401

    async with async_session_factory() as session:
        count = await generate_seed_data(session)
        print(f"Seeded {count} listings with analyses.")


if __name__ == "__main__":
    asyncio.run(main())
