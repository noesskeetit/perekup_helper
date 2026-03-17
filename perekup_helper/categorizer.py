"""AI-категоризация авто-объявлений через Claude API."""

from __future__ import annotations

import json
import logging

import anthropic

from perekup_helper.models import (
    CATEGORY_BASE_SCORES,
    CarCategory,
    CategoryResult,
    ListingDescription,
    ScoreResult,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
Ты — эксперт-перекупщик автомобилей. Анализируй описания объявлений о продаже авто \
и определяй категорию, ключевые флаги и потенциальные проблемы.

Категории:
- clean: Ровная — чистые документы, нормальный кузов, всё в порядке
- damaged_body: Кривой кузов — серьёзные повреждения кузова, может не встать на учёт
- document_issues: Проблемы с документами — нет ПТС, утилизация, запрет регистрации
- owner_debtor: Собственник-должник — кредиты, задолженности, залог, арест
- complex_profitable: Сложная но выгодная — цена сильно ниже рынка, но нужно повозиться \
(мелкий ремонт, оформление и т.д.)
- junk: Откровенный мусор — не на ходу, гнилой кузов, нет смысла связываться

Ключевые флаги для извлечения (если присутствуют в тексте):
- "звонить понимающим" — продавец прячет проблемы
- "не на ходу" — машина не едет
- "требует вложений" — нужен ремонт
- "срочно" — возможно выгодная цена
- "торг" / "торг уместен" — можно сбить цену
- "без ПТС" — проблемы с документами
- "по запчастям" / "на запчасти" — только на разбор
- "после ДТП" — была авария
- "залог" / "кредит" — финансовые обременения
- "запрет на рег. действия" — ограничения
- "1 собственник" / "один хозяин" — плюс
- "гаражное хранение" — плюс
- "не бита не крашена" — плюс (но может быть враньём)

Также обращай внимание на неявные сигналы: "для тех кто понимает", "знающим скидка", \
"цена ниже рынка не просто так" и подобные."""

USER_PROMPT_TEMPLATE = """\
Проанализируй описание объявления о продаже автомобиля и верни результат в формате JSON.

Описание:
---
{text}
---

Верни JSON строго в таком формате (без markdown, без ```):
{{
  "category": "<одна из: clean, damaged_body, document_issues, owner_debtor, complex_profitable, junk>",
  "confidence": <число от 0.0 до 1.0>,
  "flags": ["флаг1", "флаг2"],
  "reasoning": "<краткое обоснование выбора категории, 1-2 предложения>"
}}"""


class Categorizer:
    """AI-категоризатор авто-объявлений через Claude API."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-20250514",
        max_tokens: int = 512,
    ) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    def categorize(self, listing: ListingDescription) -> CategoryResult:
        """Категоризировать одно объявление."""
        message = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": USER_PROMPT_TEMPLATE.format(text=listing.text),
                }
            ],
        )

        raw_text = message.content[0].text  # type: ignore[union-attr]
        return self._parse_response(raw_text)

    def categorize_and_score(self, listing: ListingDescription) -> ScoreResult:
        """Категоризировать объявление и посчитать скоринг привлекательности."""
        category_result = self.categorize(listing)
        price_ratio = _compute_price_ratio(listing.price, listing.market_price)
        score = _compute_attractiveness(category_result, price_ratio)

        return ScoreResult(
            listing_id=listing.id,
            category_result=category_result,
            price_ratio=price_ratio,
            attractiveness_score=score,
        )

    @staticmethod
    def _parse_response(raw: str) -> CategoryResult:
        """Парсинг JSON-ответа от Claude."""
        cleaned = raw.strip()
        # Убираем возможные markdown-обёртки
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [line for line in lines if not line.strip().startswith("```")]
            cleaned = "\n".join(lines)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            logger.error("Не удалось распарсить ответ Claude: %s", raw[:200])
            raise ValueError(f"Невалидный JSON от Claude: {exc}") from exc

        # Валидация категории
        try:
            category = CarCategory(data["category"])
        except (KeyError, ValueError) as exc:
            raise ValueError(f"Неизвестная категория: {data.get('category')}") from exc

        return CategoryResult(
            category=category,
            confidence=float(data.get("confidence", 0.5)),
            flags=data.get("flags", []),
            reasoning=data.get("reasoning", ""),
        )


def _compute_price_ratio(price: int | None, market_price: int | None) -> float | None:
    """Отношение цены к рынку. < 1.0 значит ниже рынка."""
    if price is None or market_price is None or market_price <= 0:
        return None
    return round(price / market_price, 3)


def _compute_attractiveness(result: CategoryResult, price_ratio: float | None) -> float:
    """Скоринг привлекательности (0..10).

    Формула: base_category_score * 5 + price_discount_bonus * 5
    - base_category_score: вес категории (0..1)
    - price_discount_bonus: чем ниже цена от рынка, тем выше бонус (0..1)
    """
    base = CATEGORY_BASE_SCORES.get(result.category, 0.5)
    category_component = base * 5.0

    if price_ratio is not None and price_ratio > 0:
        # price_ratio < 1.0 → скидка; 0.5 = -50% от рынка → максимальный бонус
        discount = max(0.0, 1.0 - price_ratio)
        price_component = min(discount * 2.0, 1.0) * 5.0
    else:
        price_component = 2.5  # нет данных о цене — нейтральный балл

    # Бонус за уверенность модели
    confidence_factor = 0.7 + 0.3 * result.confidence

    score = (category_component + price_component) * confidence_factor
    return round(min(max(score, 0.0), 10.0), 1)
