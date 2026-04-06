"""Модели данных для AI-категоризации авто-объявлений."""

from __future__ import annotations

import logging
from enum import Enum

from pydantic import BaseModel, Field


class CarCategory(str, Enum):
    """Категории авто по состоянию/документам.

    Values match AnalysisCategory in app/models/listing.py (the DB enum).
    """

    CLEAN = "clean"
    """Ровная — чистые документы, нормальный кузов."""

    DAMAGED_BODY = "damaged_body"
    """Кривой кузов — может не встать на учёт."""

    BAD_DOCS = "bad_docs"
    """Проблемы с документами — нет ПТС, запрет регистрации."""

    DEBTOR = "debtor"
    """Собственник-должник — кредиты, задолженности, залог, арест."""

    COMPLEX_BUT_PROFITABLE = "complex_but_profitable"
    """Сложная но выгодная — цена сильно ниже рынка, но нужно повозиться."""


# Mapping from old/mismatched category strings to correct CarCategory values.
# Used for fuzzy matching when LLM returns a legacy or slightly wrong category name.
CATEGORY_ALIASES: dict[str, CarCategory] = {
    # exact matches
    "clean": CarCategory.CLEAN,
    "damaged_body": CarCategory.DAMAGED_BODY,
    "bad_docs": CarCategory.BAD_DOCS,
    "debtor": CarCategory.DEBTOR,
    "complex_but_profitable": CarCategory.COMPLEX_BUT_PROFITABLE,
    # old prompt values (legacy)
    "document_issues": CarCategory.BAD_DOCS,
    "owner_debtor": CarCategory.DEBTOR,
    "complex_profitable": CarCategory.COMPLEX_BUT_PROFITABLE,
    # other common LLM variations
    "junk": CarCategory.DAMAGED_BODY,
    "bad_documents": CarCategory.BAD_DOCS,
    "docs_issues": CarCategory.BAD_DOCS,
    "doc_issues": CarCategory.BAD_DOCS,
    "debt": CarCategory.DEBTOR,
    "complex": CarCategory.COMPLEX_BUT_PROFITABLE,
    "profitable": CarCategory.COMPLEX_BUT_PROFITABLE,
}


def resolve_category(raw_value: str) -> CarCategory:
    """Resolve a raw category string to CarCategory, using aliases for fuzzy matching.

    Tries exact enum match first, then alias lookup, then substring matching.
    Falls back to CLEAN if nothing matches.
    """
    normalized = raw_value.strip().lower().replace("-", "_").replace(" ", "_")

    # 1. Exact enum value match
    try:
        return CarCategory(normalized)
    except ValueError:
        pass

    # 2. Alias lookup
    if normalized in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[normalized]

    # 3. Substring / partial match in aliases
    for alias, cat in CATEGORY_ALIASES.items():
        if alias in normalized or normalized in alias:
            return cat

    logging.getLogger(__name__).warning("Unknown category '%s', falling back to CLEAN", raw_value)
    return CarCategory.CLEAN


CATEGORY_LABELS: dict[CarCategory, str] = {
    CarCategory.CLEAN: "Ровная (чистые документы, нормальный кузов)",
    CarCategory.DAMAGED_BODY: "Кривой кузов (может не встать на учёт)",
    CarCategory.BAD_DOCS: "Проблемы с документами",
    CarCategory.DEBTOR: "Собственник-должник (кредиты, задолженности)",
    CarCategory.COMPLEX_BUT_PROFITABLE: "Сложная но выгодная (цена ниже рынка, нужно повозиться)",
}

# Скоринговые веса по категориям (базовый балл категории от 0 до 1)
CATEGORY_BASE_SCORES: dict[CarCategory, float] = {
    CarCategory.CLEAN: 1.0,
    CarCategory.COMPLEX_BUT_PROFITABLE: 0.7,
    CarCategory.DAMAGED_BODY: 0.3,
    CarCategory.BAD_DOCS: 0.2,
    CarCategory.DEBTOR: 0.15,
}


class ListingDescription(BaseModel):
    """Входные данные: описание объявления для анализа."""

    id: str = Field(description="Уникальный ID объявления")
    text: str = Field(description="Текст описания объявления")
    price: int | None = Field(default=None, description="Цена из объявления, руб.")
    market_price: int | None = Field(default=None, description="Среднерыночная цена аналога, руб.")


class CategoryResult(BaseModel):
    """Результат AI-категоризации одного объявления."""

    category: CarCategory = Field(description="Определённая категория")
    confidence: float = Field(ge=0.0, le=1.0, description="Уверенность модели (0..1)")
    flags: list[str] = Field(
        default_factory=list,
        description="Ключевые флаги из описания",
    )
    reasoning: str = Field(description="Краткое обоснование категории")


class ScoreResult(BaseModel):
    """Итоговый результат: категория + скоринг привлекательности."""

    listing_id: str
    category_result: CategoryResult
    price_ratio: float | None = Field(
        default=None,
        description="Отношение цены к рынку (< 1.0 = ниже рынка)",
    )
    attractiveness_score: float = Field(ge=0.0, le=10.0, description="Итоговый балл привлекательности (0..10)")
