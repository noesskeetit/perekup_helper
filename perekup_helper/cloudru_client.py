"""Cloud.ru FM API client for AI categorization.

Two-stage pipeline:
1. DeepSeek-OCR-2 (VLM, 8k context) — describes car photos
2. GLM-4.7 (thinking model, free) — categorizes based on text + photo description

GLM-4.7 is a thinking model: it reasons in 'reasoning' field and outputs final answer in 'content'.
"""

from __future__ import annotations

import json
import logging
import os
import time

import httpx

from perekup_helper.models import (
    CATEGORY_BASE_SCORES,
    CarCategory,
    CategoryResult,
    ListingDescription,
    ScoreResult,
)

logger = logging.getLogger(__name__)

CLOUDRU_API_URL = os.environ.get(
    "CLOUDRU_FM_URL",
    "https://foundation-models.api.cloud.ru/v1/chat/completions",
)

CATEGORIZE_PROMPT = """\
Категоризируй авто-объявление. Ответь ТОЛЬКО JSON без markdown:
{{"category": "<clean|damaged_body|document_issues|owner_debtor|complex_profitable|junk>", "confidence": <0.0-1.0>, "flags": ["флаг1", "флаг2"], "reasoning": "<1-2 предложения>"}}

Категории:
- clean: чистые документы, нормальный кузов
- damaged_body: серьёзные повреждения кузова
- document_issues: нет ПТС, запрет регистрации
- owner_debtor: кредиты, залог, арест
- complex_profitable: цена ниже рынка, но нужно повозиться
- junk: не на ходу, гнилой кузов

Объявление:
{text}"""

DESCRIBE_IMAGE_PROMPT = "Опиши автомобиль на фото кратко: состояние кузова, видимые повреждения, цвет, тип кузова. 2-3 предложения."


class CloudRuCategorizer:
    """AI categorizer using Cloud.ru Foundation Models."""

    def __init__(
        self,
        api_key: str | None = None,
        ocr_model: str = "deepseek-ai/DeepSeek-OCR-2",
        text_model: str = "zai-org/GLM-4.7",
    ):
        self._api_key = api_key or os.environ.get("CLOUDRU_FM_API_KEY", "")
        self._ocr_model = ocr_model
        self._text_model = text_model

    def categorize(self, listing: ListingDescription) -> CategoryResult:
        """Categorize a listing using GLM-4.7."""
        prompt = CATEGORIZE_PROMPT.format(text=listing.text)
        raw = self._call_api(self._text_model, [{"role": "user", "content": prompt}], max_tokens=1000)
        return self._parse_response(raw)

    def categorize_with_image(self, listing: ListingDescription, image_url: str) -> CategoryResult:
        """Describe image with DeepSeek-OCR-2, then categorize with GLM-4.7."""
        # Stage 1: describe image
        photo_desc = self.describe_image(image_url)

        # Stage 2: categorize with both text and photo description
        full_text = listing.text
        if photo_desc:
            full_text += f"\n\nОписание фото: {photo_desc}"

        prompt = CATEGORIZE_PROMPT.format(text=full_text)
        raw = self._call_api(self._text_model, [{"role": "user", "content": prompt}], max_tokens=1000)
        return self._parse_response(raw)

    def describe_image(self, image_url: str) -> str | None:
        """Use DeepSeek-OCR-2 to describe a car photo."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": DESCRIBE_IMAGE_PROMPT},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ]
        try:
            return self._call_api(self._ocr_model, messages, max_tokens=300)
        except Exception:
            logger.warning("Failed to describe image: %s", image_url[:80], exc_info=True)
            return None

    def categorize_and_score(self, listing: ListingDescription, image_url: str | None = None) -> ScoreResult:
        """Categorize + compute attractiveness score."""
        if image_url:
            cat = self.categorize_with_image(listing, image_url)
        else:
            cat = self.categorize(listing)

        price_ratio = _compute_price_ratio(listing.price, listing.market_price)
        score = _compute_attractiveness(cat, price_ratio)

        return ScoreResult(
            listing_id=listing.id,
            category_result=cat,
            price_ratio=price_ratio,
            attractiveness_score=score,
        )

    def _call_api(self, model: str, messages: list[dict], max_tokens: int = 1000) -> str:
        """Call Cloud.ru FM API with retry."""
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.1,
        }

        for attempt in range(3):
            try:
                resp = httpx.post(CLOUDRU_API_URL, json=payload, headers=headers, timeout=90)
                if resp.status_code == 429:
                    wait = 5 * (attempt + 1)
                    logger.warning("Cloud.ru rate limit, waiting %ds", wait)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                choice = data["choices"][0]["message"]
                # GLM-4.7 is a thinking model: final JSON in content, chain-of-thought in reasoning
                content = choice.get("content") or ""
                reasoning = choice.get("reasoning") or ""
                # Prefer content if it looks like JSON, otherwise extract JSON from reasoning
                if content and ("{" in content):
                    return content
                if reasoning and ("{" in reasoning):
                    # Extract last JSON block from reasoning
                    import re
                    json_blocks = re.findall(r'\{[^{}]*"category"[^{}]*\}', reasoning, re.DOTALL)
                    if json_blocks:
                        return json_blocks[-1]
                return content or reasoning
            except Exception as e:
                logger.warning("Cloud.ru API error (attempt %d): %s", attempt + 1, e)
                if attempt < 2:
                    time.sleep(3)

        raise RuntimeError("Cloud.ru FM API failed after retries")

    @staticmethod
    def _parse_response(raw: str) -> CategoryResult:
        """Parse JSON response into CategoryResult."""
        import re

        cleaned = raw.strip()

        # Remove markdown fences
        if "```json" in cleaned:
            start = cleaned.index("```json") + 7
            end = cleaned.index("```", start) if "```" in cleaned[start:] else len(cleaned)
            cleaned = cleaned[start:end].strip()
        elif cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines)

        # Try direct parse first
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            # Extract JSON object with "category" key from text
            # Handle multi-line JSON blocks
            json_match = re.search(r'\{[^{}]*?"category"\s*:\s*"[^"]+?"[^}]*?\}', cleaned, re.DOTALL)
            if json_match:
                try:
                    data = json.loads(json_match.group())
                except json.JSONDecodeError:
                    data = None
            else:
                data = None

        if data is None:
            logger.warning("Failed to parse Cloud.ru response: %s", raw[:200])
            return CategoryResult(category=CarCategory.CLEAN, confidence=0.3, flags=[], reasoning="parse error")

        try:
            category = CarCategory(data["category"])
        except (KeyError, ValueError):
            category = CarCategory.CLEAN

        return CategoryResult(
            category=category,
            confidence=float(data.get("confidence", 0.5)),
            flags=data.get("flags", []),
            reasoning=data.get("reasoning", ""),
        )


def _compute_price_ratio(price: int | None, market_price: int | None) -> float | None:
    if price is None or market_price is None or market_price <= 0:
        return None
    return round(price / market_price, 3)


def _compute_attractiveness(result: CategoryResult, price_ratio: float | None) -> float:
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
