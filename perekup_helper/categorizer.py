"""AI-категоризация авто-объявлений через OpenRouter API (async)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re

import httpx

from perekup_helper.models import (
    CATEGORY_BASE_SCORES,
    CarCategory,
    CategoryResult,
    ListingDescription,
    ScoreResult,
    resolve_category,
)

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM_PROMPT = """\
Ты — эксперт-перекупщик автомобилей. Анализируй описания объявлений о продаже авто \
и определяй категорию, ключевые флаги и потенциальные проблемы.

Категории (используй ТОЛЬКО эти значения):
- clean: Ровная — чистые документы, нормальный кузов, всё в порядке
- damaged_body: Кривой кузов — серьёзные повреждения кузова, может не встать на учёт
- bad_docs: Проблемы с документами — нет ПТС, утилизация, запрет регистрации
- debtor: Собственник-должник — кредиты, задолженности, залог, арест
- complex_but_profitable: Сложная но выгодная — цена сильно ниже рынка, но нужно повозиться \
(мелкий ремонт, оформление и т.д.)

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
  "category": "<одна из: clean, damaged_body, bad_docs, debtor, complex_but_profitable>",
  "confidence": <число от 0.0 до 1.0>,
  "flags": ["флаг1", "флаг2"],
  "reasoning": "<краткое обоснование выбора категории, 1-2 предложения>"
}}"""


class Categorizer:
    """AI-категоризатор авто-объявлений через OpenRouter API (async)."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "qwen/qwen3.6-plus:free",
        max_tokens: int = 512,
    ) -> None:
        self._api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self._model = model
        self._max_tokens = max_tokens

    async def categorize(self, listing: ListingDescription) -> CategoryResult:
        """Категоризировать одно объявление."""
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT_TEMPLATE.format(text=listing.text)},
        ]

        raw_text = await self._call_openrouter(messages)
        return self._parse_response(raw_text)

    async def categorize_with_image(self, listing: ListingDescription, image_url: str) -> CategoryResult:
        """Категоризировать объявление с фотографией (мультимодальность)."""
        content = [
            {"type": "text", "text": USER_PROMPT_TEMPLATE.format(text=listing.text)},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]

        raw_text = await self._call_openrouter(messages)
        return self._parse_response(raw_text)

    async def categorize_and_score(self, listing: ListingDescription, image_url: str | None = None) -> ScoreResult:
        """Категоризировать объявление и посчитать скоринг привлекательности."""
        if image_url:
            category_result = await self.categorize_with_image(listing, image_url)
        else:
            category_result = await self.categorize(listing)

        price_ratio = _compute_price_ratio(listing.price, listing.market_price)
        score = _compute_attractiveness(category_result, price_ratio)

        return ScoreResult(
            listing_id=listing.id,
            category_result=category_result,
            price_ratio=price_ratio,
            attractiveness_score=score,
        )

    async def _call_openrouter(self, messages: list[dict], max_retries: int = 3) -> str:
        """Call OpenRouter API with retry on rate limits (async)."""
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "messages": messages,
            "max_tokens": self._max_tokens,
            "temperature": 0.1,
        }

        async with httpx.AsyncClient() as client:
            for attempt in range(1, max_retries + 1):
                response = await client.post(
                    OPENROUTER_URL,
                    json=payload,
                    headers=headers,
                    timeout=60,
                )
                if response.status_code == 429:
                    wait = min(attempt * 10, 30)
                    logger.warning("OpenRouter rate limit, waiting %ds (attempt %d/%d)", wait, attempt, max_retries)
                    await asyncio.sleep(wait)
                    continue
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]

        raise RuntimeError("OpenRouter rate limit exceeded after retries")

    @staticmethod
    def _parse_response(raw: str) -> CategoryResult:
        """Parse JSON response with robust extraction and fuzzy category matching."""
        cleaned = raw.strip()

        # Strategy 1: Remove markdown code fences
        if "```json" in cleaned:
            match = re.search(r"```json\s*(.*?)\s*```", cleaned, re.DOTALL)
            if match:
                cleaned = match.group(1).strip()
        elif cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [line for line in lines if not line.strip().startswith("```")]
            cleaned = "\n".join(lines).strip()

        # Strategy 2: Try direct JSON parse
        data = _try_parse_json(cleaned)

        # Strategy 3: Find JSON object anywhere in the text
        if data is None:
            json_match = re.search(r"\{[^{}]*\"category\"[^{}]*\}", cleaned, re.DOTALL)
            if json_match:
                data = _try_parse_json(json_match.group())

        # Strategy 4: Find JSON object with nested arrays (flags)
        if data is None:
            json_match = re.search(r"\{.*?\"category\"\s*:.*?\}", cleaned, re.DOTALL)
            if json_match:
                data = _try_parse_json(json_match.group())

        if data is None:
            logger.error(
                "Failed to parse LLM response as JSON. Raw response (first 500 chars): %s",
                raw[:500],
            )
            return CategoryResult(
                category=CarCategory.CLEAN,
                confidence=0.3,
                flags=[],
                reasoning="parse error",
            )

        # Resolve category with fuzzy matching
        raw_category = data.get("category", "clean")
        category = resolve_category(str(raw_category))

        return CategoryResult(
            category=category,
            confidence=float(data.get("confidence", 0.5)),
            flags=data.get("flags", []),
            reasoning=data.get("reasoning", ""),
        )


def _try_parse_json(text: str) -> dict | None:
    """Try to parse text as JSON, return None on failure."""
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _compute_price_ratio(price: int | None, market_price: int | None) -> float | None:
    """Отношение цены к рынку. < 1.0 значит ниже рынка."""
    if price is None or market_price is None or market_price <= 0:
        return None
    return round(price / market_price, 3)


def _compute_attractiveness(result: CategoryResult, price_ratio: float | None) -> float:
    """Скоринг привлекательности (0..10)."""
    base = CATEGORY_BASE_SCORES.get(result.category, 0.5)
    category_component = base * 5.0

    if price_ratio is not None and price_ratio > 0:
        discount = max(0.0, 1.0 - price_ratio)
        price_component = min(discount * 2.0, 1.0) * 5.0
    else:
        price_component = 2.5

    confidence_factor = 0.7 + 0.3 * result.confidence
    score = (category_component + price_component) * confidence_factor
    return round(min(max(score, 0.0), 10.0), 1)
