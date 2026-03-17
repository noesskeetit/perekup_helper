"""Модели данных для AI-категоризации авто-объявлений."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class CarCategory(str, Enum):
    """Категории авто по состоянию/документам."""

    CLEAN = "clean"
    """Ровная — чистые документы, нормальный кузов."""

    DAMAGED_BODY = "damaged_body"
    """Кривой кузов — может не встать на учёт."""

    DOCUMENT_ISSUES = "document_issues"
    """Проблемы с документами."""

    OWNER_DEBTOR = "owner_debtor"
    """Собственник-должник — кредиты, задолженности."""

    COMPLEX_PROFITABLE = "complex_profitable"
    """Сложная но выгодная — цена сильно ниже рынка, но нужно повозиться."""

    JUNK = "junk"
    """Откровенный мусор."""


CATEGORY_LABELS: dict[CarCategory, str] = {
    CarCategory.CLEAN: "Ровная (чистые документы, нормальный кузов)",
    CarCategory.DAMAGED_BODY: "Кривой кузов (может не встать на учёт)",
    CarCategory.DOCUMENT_ISSUES: "Проблемы с документами",
    CarCategory.OWNER_DEBTOR: "Собственник-должник (кредиты, задолженности)",
    CarCategory.COMPLEX_PROFITABLE: "Сложная но выгодная (цена ниже рынка, нужно повозиться)",
    CarCategory.JUNK: "Откровенный мусор",
}

# Скоринговые веса по категориям (базовый балл категории от 0 до 1)
CATEGORY_BASE_SCORES: dict[CarCategory, float] = {
    CarCategory.CLEAN: 1.0,
    CarCategory.COMPLEX_PROFITABLE: 0.7,
    CarCategory.DAMAGED_BODY: 0.3,
    CarCategory.DOCUMENT_ISSUES: 0.2,
    CarCategory.OWNER_DEBTOR: 0.15,
    CarCategory.JUNK: 0.0,
}


class ListingDescription(BaseModel):
    """Входные данные: описание объявления для анализа."""

    id: str = Field(description="Уникальный ID объявления")
    text: str = Field(description="Текст описания объявления")
    price: Optional[int] = Field(default=None, description="Цена из объявления, руб.")
    market_price: Optional[int] = Field(
        default=None, description="Среднерыночная цена аналога, руб."
    )


class CategoryResult(BaseModel):
    """Результат AI-категоризации одного объявления."""

    category: CarCategory = Field(description="Определённая категория")
    confidence: float = Field(
        ge=0.0, le=1.0, description="Уверенность модели (0..1)"
    )
    flags: list[str] = Field(
        default_factory=list,
        description="Ключевые флаги из описания",
    )
    reasoning: str = Field(description="Краткое обоснование категории")


class ScoreResult(BaseModel):
    """Итоговый результат: категория + скоринг привлекательности."""

    listing_id: str
    category_result: CategoryResult
    price_ratio: Optional[float] = Field(
        default=None,
        description="Отношение цены к рынку (< 1.0 = ниже рынка)",
    )
    attractiveness_score: float = Field(
        ge=0.0, le=10.0, description="Итоговый балл привлекательности (0..10)"
    )
