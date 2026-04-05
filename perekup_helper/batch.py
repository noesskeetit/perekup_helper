"""Batch processing для категоризации объявлений через OpenRouter."""

from __future__ import annotations

import json
import logging
import time

import httpx

from perekup_helper.categorizer import (
    OPENROUTER_URL,
    SYSTEM_PROMPT,
    Categorizer,
    _compute_attractiveness,
    _compute_price_ratio,
)
from perekup_helper.models import (
    CategoryResult,
    ListingDescription,
    ScoreResult,
)

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 10

BATCH_USER_PROMPT_TEMPLATE = """\
Проанализируй описания {count} объявлений о продаже автомобилей.
Для каждого верни результат категоризации.

Объявления:
{listings_block}

Верни JSON-массив строго в таком формате (без markdown, без ```):
[
  {{
    "id": "<id объявления>",
    "category": "<одна из: clean, damaged_body, document_issues, owner_debtor, complex_profitable, junk>",
    "confidence": <число от 0.0 до 1.0>,
    "flags": ["флаг1", "флаг2"],
    "reasoning": "<краткое обоснование, 1-2 предложения>"
  }}
]

ВАЖНО: верни ровно {count} элементов в массиве, по одному на каждое объявление. \
ID должны совпадать с переданными."""


class BatchProcessor:
    """Групповая обработка объявлений через OpenRouter API."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "qwen/qwen3.6-plus:free",
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_tokens: int = 4096,
        rate_limit_delay: float = 1.0,
        max_retries: int = 2,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._batch_size = batch_size
        self._max_tokens = max_tokens
        self._rate_limit_delay = rate_limit_delay
        self._max_retries = max_retries
        self._single_categorizer = Categorizer(api_key=api_key, model=model)

    def process(self, listings: list[ListingDescription]) -> list[ScoreResult]:
        """Обработать список объявлений батчами."""
        results: list[ScoreResult] = []

        for i in range(0, len(listings), self._batch_size):
            batch = listings[i : i + self._batch_size]
            logger.info(
                "Обработка батча %d/%d (%d объявлений)",
                i // self._batch_size + 1,
                (len(listings) + self._batch_size - 1) // self._batch_size,
                len(batch),
            )

            try:
                batch_results = self._process_batch(batch)
                results.extend(batch_results)
            except Exception:
                logger.warning(
                    "Батч %d не удался, fallback на поштучную обработку",
                    i // self._batch_size + 1,
                    exc_info=True,
                )
                for listing in batch:
                    try:
                        results.append(self._process_single(listing))
                    except Exception:
                        logger.warning("Не удалось категоризировать %s", listing.id, exc_info=True)

            if i + self._batch_size < len(listings):
                time.sleep(self._rate_limit_delay)

        return results

    def _process_batch(self, batch: list[ListingDescription]) -> list[ScoreResult]:
        listings_block = "\n\n".join(f"--- Объявление ID: {listing.id} ---\n{listing.text}" for listing in batch)
        prompt = BATCH_USER_PROMPT_TEMPLATE.format(count=len(batch), listings_block=listings_block)

        raw_text = self._call_api_with_retry(prompt)
        category_map = self._parse_batch_response(raw_text)

        results: list[ScoreResult] = []
        for listing in batch:
            if listing.id in category_map:
                cat_result = category_map[listing.id]
            else:
                logger.warning("ID %s не найден в ответе батча, fallback", listing.id)
                cat_result = self._single_categorizer.categorize(listing)

            price_ratio = _compute_price_ratio(listing.price, listing.market_price)
            score = _compute_attractiveness(cat_result, price_ratio)
            results.append(
                ScoreResult(
                    listing_id=listing.id,
                    category_result=cat_result,
                    price_ratio=price_ratio,
                    attractiveness_score=score,
                )
            )

        return results

    def _call_api_with_retry(self, user_prompt: str) -> str:
        """Call OpenRouter API with retry."""
        import os

        api_key = self._api_key or os.environ.get("OPENROUTER_API_KEY", "")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                response = httpx.post(
                    OPENROUTER_URL,
                    json={
                        "model": self._model,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt},
                        ],
                        "max_tokens": self._max_tokens,
                        "temperature": 0.1,
                    },
                    headers=headers,
                    timeout=90,
                )
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]

            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    logger.warning("Rate limit (попытка %d/%d)", attempt, self._max_retries)
                    time.sleep(self._rate_limit_delay * attempt * 3)
                else:
                    last_exc = exc
                    logger.warning("API ошибка %d (попытка %d/%d)", exc.response.status_code, attempt, self._max_retries)
                    time.sleep(self._rate_limit_delay * attempt)
            except Exception as exc:
                last_exc = exc
                logger.warning("Ошибка запроса (попытка %d/%d): %s", attempt, self._max_retries, exc)
                time.sleep(self._rate_limit_delay * attempt)

        raise RuntimeError(f"Не удалось вызвать OpenRouter API за {self._max_retries} попыток") from last_exc

    def _process_single(self, listing: ListingDescription) -> ScoreResult:
        return self._single_categorizer.categorize_and_score(listing)

    @staticmethod
    def _parse_batch_response(raw: str) -> dict[str, CategoryResult]:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [line for line in lines if not line.strip().startswith("```")]
            cleaned = "\n".join(lines)
        if "```json" in cleaned:
            start = cleaned.index("```json") + 7
            end = cleaned.index("```", start) if "```" in cleaned[start:] else len(cleaned)
            cleaned = cleaned[start:end].strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            logger.error("Не удалось распарсить batch-ответ: %s", raw[:300])
            raise ValueError(f"Невалидный batch JSON: {exc}") from exc

        if not isinstance(data, list):
            raise ValueError("Ожидался JSON-массив в batch-ответе")

        result: dict[str, CategoryResult] = {}
        for item in data:
            try:
                cat_result = Categorizer._parse_response(json.dumps(item))
                listing_id = item.get("id", "")
                result[listing_id] = cat_result
            except (ValueError, KeyError) as exc:
                logger.warning("Пропуск элемента в батче: %s", exc)

        return result
