"""Batch processing для экономии API calls при категоризации объявлений."""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

import anthropic

from perekup_helper.categorizer import (
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

# Макс. объявлений в одном запросе к Claude (чтобы не превысить контекст)
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
    """Групповая обработка объявлений для экономии API-вызовов."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "claude-sonnet-4-20250514",
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_tokens: int = 4096,
        rate_limit_delay: float = 0.5,
        max_retries: int = 2,
    ) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._batch_size = batch_size
        self._max_tokens = max_tokens
        self._rate_limit_delay = rate_limit_delay
        self._max_retries = max_retries
        self._single_categorizer = Categorizer(
            api_key=api_key, model=model
        )

    def process(self, listings: list[ListingDescription]) -> list[ScoreResult]:
        """Обработать список объявлений батчами.

        Группирует объявления по batch_size, отправляет по одному
        запросу на группу. При ошибке в батче — fallback на поштучную обработку.
        """
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
                    results.append(self._process_single(listing))

            # Rate limiting между батчами
            if i + self._batch_size < len(listings):
                time.sleep(self._rate_limit_delay)

        return results

    def _process_batch(
        self, batch: list[ListingDescription]
    ) -> list[ScoreResult]:
        """Обработать один батч объявлений одним API-вызовом."""
        listings_block = "\n\n".join(
            f"--- Объявление ID: {listing.id} ---\n{listing.text}"
            for listing in batch
        )

        prompt = BATCH_USER_PROMPT_TEMPLATE.format(
            count=len(batch), listings_block=listings_block
        )

        raw_text = self._call_api_with_retry(prompt)
        category_map = self._parse_batch_response(raw_text)

        results: list[ScoreResult] = []
        for listing in batch:
            if listing.id in category_map:
                cat_result = category_map[listing.id]
            else:
                logger.warning(
                    "ID %s не найден в ответе батча, fallback", listing.id
                )
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
        """Вызов Claude API с retry при ошибках."""
        last_exc: Optional[Exception] = None
        for attempt in range(1, self._max_retries + 1):
            try:
                message = self._client.messages.create(
                    model=self._model,
                    max_tokens=self._max_tokens,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_prompt}],
                )
                return message.content[0].text  # type: ignore[union-attr]
            except anthropic.RateLimitError:
                logger.warning(
                    "Rate limit (попытка %d/%d), ждём...",
                    attempt,
                    self._max_retries,
                )
                time.sleep(self._rate_limit_delay * attempt * 2)
                last_exc = None
            except anthropic.APIError as exc:
                logger.warning(
                    "API ошибка (попытка %d/%d): %s",
                    attempt,
                    self._max_retries,
                    exc,
                )
                last_exc = exc
                if attempt < self._max_retries:
                    time.sleep(self._rate_limit_delay * attempt)

        raise RuntimeError(
            f"Не удалось вызвать Claude API за {self._max_retries} попыток"
        ) from last_exc

    def _process_single(self, listing: ListingDescription) -> ScoreResult:
        """Fallback: поштучная обработка через Categorizer."""
        return self._single_categorizer.categorize_and_score(listing)

    @staticmethod
    def _parse_batch_response(raw: str) -> dict[str, CategoryResult]:
        """Парсинг JSON-массива из batch-ответа Claude."""
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines)

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
