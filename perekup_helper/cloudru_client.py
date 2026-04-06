"""Cloud.ru FM API client for AI categorization (async).

Two-stage pipeline:
1. DeepSeek-OCR-2 (VLM, 8k context) — describes car photos
2. GLM-4.7 (thinking model, free) — categorizes based on text + photo description

GLM-4.7 is a thinking model: it reasons in 'reasoning' field and outputs final answer in 'content'.
"""

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

CLOUDRU_API_URL = os.environ.get(
    "CLOUDRU_FM_URL",
    "https://foundation-models.api.cloud.ru/v1/chat/completions",
)

CATEGORIZE_PROMPT = """\
Категоризируй авто. JSON, без markdown:
{{"category":"clean|damaged_body|bad_docs|debtor|complex_but_profitable","confidence":0.0-1.0,"flags":[],"reasoning":"кратко"}}

clean=ок, damaged_body=битая, bad_docs=нет ПТС, debtor=залог/арест, complex_but_profitable=дешево но сложно.

{text}"""

DESCRIBE_IMAGE_PROMPT = "Опиши автомобиль на фото кратко: состояние кузова, видимые повреждения, цвет, тип кузова. 2-3 предложения."


class CloudRuCategorizer:
    """AI categorizer using Cloud.ru Foundation Models (async)."""

    def __init__(
        self,
        api_key: str | None = None,
        ocr_model: str = "deepseek-ai/DeepSeek-OCR-2",
        text_model: str = "zai-org/GLM-4.7",
    ):
        self._api_key = api_key or os.environ.get("CLOUDRU_FM_API_KEY", "")
        self._ocr_model = ocr_model
        self._text_model = text_model

    async def categorize(self, listing: ListingDescription) -> CategoryResult:
        """Categorize a listing using GLM-4.7."""
        prompt = CATEGORIZE_PROMPT.format(text=listing.text)
        raw = await self._call_api(self._text_model, [{"role": "user", "content": prompt}], max_tokens=1000)
        return self._parse_response(raw)

    async def categorize_with_image(self, listing: ListingDescription, image_url: str) -> CategoryResult:
        """Describe image with DeepSeek-OCR-2, then categorize with GLM-4.7."""
        # Stage 1: describe image
        photo_desc = await self.describe_image(image_url)

        # Stage 2: categorize with both text and photo description
        full_text = listing.text
        if photo_desc:
            full_text += f"\n\nОписание фото: {photo_desc}"

        prompt = CATEGORIZE_PROMPT.format(text=full_text)
        raw = await self._call_api(self._text_model, [{"role": "user", "content": prompt}], max_tokens=1000)
        return self._parse_response(raw)

    async def describe_image(self, image_url: str) -> str | None:
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
            return await self._call_api(self._ocr_model, messages, max_tokens=300)
        except Exception:
            logger.warning("Failed to describe image: %s", image_url[:80], exc_info=True)
            return None

    async def categorize_and_score(self, listing: ListingDescription, image_url: str | None = None) -> ScoreResult:
        """Categorize + compute attractiveness score."""
        if image_url:
            cat = await self.categorize_with_image(listing, image_url)
        else:
            cat = await self.categorize(listing)

        price_ratio = _compute_price_ratio(listing.price, listing.market_price)
        score = _compute_attractiveness(cat, price_ratio)

        return ScoreResult(
            listing_id=listing.id,
            category_result=cat,
            price_ratio=price_ratio,
            attractiveness_score=score,
        )

    async def _call_api(self, model: str, messages: list[dict], max_tokens: int = 1000) -> str:
        """Call Cloud.ru FM API with retry (async)."""
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

        async with httpx.AsyncClient() as client:
            for attempt in range(3):
                try:
                    resp = await client.post(CLOUDRU_API_URL, json=payload, headers=headers, timeout=90)
                    if resp.status_code == 429:
                        wait = 5 * (attempt + 1)
                        logger.warning("Cloud.ru rate limit, waiting %ds", wait)
                        await asyncio.sleep(wait)
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    choice = data["choices"][0]["message"]
                    # GLM-4.7 is a thinking model: final JSON in content, chain-of-thought in reasoning
                    content = choice.get("content") or ""
                    reasoning = choice.get("reasoning") or ""
                    # Check both fields for JSON with "category" key
                    for text in [content, reasoning]:
                        if text and '"category"' in text:
                            # Find JSON object containing "category"
                            start = text.find("{")
                            if start >= 0:
                                # Find matching closing brace
                                depth = 0
                                for i, ch in enumerate(text[start:], start):
                                    if ch == "{":
                                        depth += 1
                                    elif ch == "}":
                                        depth -= 1
                                        if depth == 0:
                                            return text[start : i + 1]
                                # No matching brace — return from { to end (truncated)
                                return text[start:]
                    return content or reasoning
                except Exception as e:
                    logger.warning("Cloud.ru API error (attempt %d): %s", attempt + 1, e)
                    if attempt < 2:
                        await asyncio.sleep(3)

        raise RuntimeError("Cloud.ru FM API failed after retries")

    @staticmethod
    def _parse_response(raw: str) -> CategoryResult:
        """Parse JSON response into CategoryResult with robust extraction and fuzzy matching."""
        cleaned = raw.strip()

        # Remove markdown fences
        if "```json" in cleaned:
            match = re.search(r"```json\s*(.*?)\s*```", cleaned, re.DOTALL)
            if match:
                cleaned = match.group(1).strip()
        elif cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines)

        # Try direct parse first
        data = _try_parse_json(cleaned)

        # Extract JSON object with "category" key from text
        if data is None:
            json_match = re.search(r'\{[^{}]*?"category"\s*:\s*"[^"]+?"[^}]*?\}', cleaned, re.DOTALL)
            if json_match:
                data = _try_parse_json(json_match.group())

        # Try finding any JSON object
        if data is None:
            json_match = re.search(r"\{.*?\"category\"\s*:.*?\}", cleaned, re.DOTALL)
            if json_match:
                data = _try_parse_json(json_match.group())

        if data is None:
            logger.error(
                "Failed to parse Cloud.ru response. Raw (first 500 chars): %s",
                raw[:500],
            )
            return CategoryResult(category=CarCategory.CLEAN, confidence=0.3, flags=[], reasoning="parse error")

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
    """Try to parse text as JSON, with repair for truncated responses."""
    # Direct parse
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, ValueError):
        pass

    # Try to repair truncated JSON (missing closing braces/quotes)
    repaired = text.rstrip()
    if repaired and not repaired.endswith("}"):
        # Truncated string value — close the quote and brace
        if repaired.count('"') % 2 != 0:
            repaired += '"'
        repaired += "}"
        try:
            result = json.loads(repaired)
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, ValueError):
            pass

    return None


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
