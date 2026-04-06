"""Тесты async batch processing."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from perekup_helper.batch import BatchProcessor
from perekup_helper.models import CarCategory, ListingDescription


def _make_batch_response(items: list[dict[str, object]]) -> str:
    return json.dumps(items, ensure_ascii=False)


class TestBatchProcessor:
    @pytest.mark.asyncio
    async def test_single_batch(self) -> None:
        response_data = [
            {
                "id": "1",
                "category": "clean",
                "confidence": 0.9,
                "flags": ["один хозяин"],
                "reasoning": "Чисто",
            },
            {
                "id": "2",
                "category": "damaged_body",
                "confidence": 0.8,
                "flags": ["после ДТП"],
                "reasoning": "Битая",
            },
        ]

        processor = BatchProcessor(api_key="test-key", batch_size=5)
        listings = [
            ListingDescription(id="1", text="Один хозяин, гараж"),
            ListingDescription(id="2", text="После ДТП, на запчасти"),
        ]

        with patch.object(
            processor,
            "_call_api_with_retry",
            new_callable=AsyncMock,
            return_value=_make_batch_response(response_data),
        ):
            results = await processor.process(listings)

        assert len(results) == 2
        assert results[0].listing_id == "1"
        assert results[0].category_result.category == CarCategory.CLEAN
        assert results[1].listing_id == "2"
        assert results[1].category_result.category == CarCategory.DAMAGED_BODY

    @pytest.mark.asyncio
    async def test_multiple_batches(self) -> None:
        batch1 = [
            {"id": "1", "category": "clean", "confidence": 0.9, "flags": [], "reasoning": "ok"},
            {"id": "2", "category": "damaged_body", "confidence": 0.7, "flags": [], "reasoning": "ok"},
        ]
        batch2 = [
            {"id": "3", "category": "debtor", "confidence": 0.8, "flags": [], "reasoning": "ok"},
        ]

        processor = BatchProcessor(api_key="test-key", batch_size=2, rate_limit_delay=0.0)
        listings = [
            ListingDescription(id="1", text="A"),
            ListingDescription(id="2", text="B"),
            ListingDescription(id="3", text="C"),
        ]

        with patch.object(
            processor,
            "_call_api_with_retry",
            new_callable=AsyncMock,
            side_effect=[_make_batch_response(batch1), _make_batch_response(batch2)],
        ):
            results = await processor.process(listings)

        assert len(results) == 3
        assert results[2].category_result.category == CarCategory.DEBTOR

    @pytest.mark.asyncio
    async def test_legacy_categories_in_batch(self) -> None:
        """Batch response with legacy category names should be resolved correctly."""
        response_data = [
            {"id": "1", "category": "document_issues", "confidence": 0.7, "flags": [], "reasoning": "ok"},
            {"id": "2", "category": "owner_debtor", "confidence": 0.8, "flags": [], "reasoning": "ok"},
            {"id": "3", "category": "complex_profitable", "confidence": 0.9, "flags": [], "reasoning": "ok"},
        ]

        processor = BatchProcessor(api_key="test-key", batch_size=10)
        listings = [
            ListingDescription(id="1", text="Без ПТС"),
            ListingDescription(id="2", text="В залоге"),
            ListingDescription(id="3", text="Срочно, торг"),
        ]

        with patch.object(
            processor,
            "_call_api_with_retry",
            new_callable=AsyncMock,
            return_value=_make_batch_response(response_data),
        ):
            results = await processor.process(listings)

        assert results[0].category_result.category == CarCategory.BAD_DOCS
        assert results[1].category_result.category == CarCategory.DEBTOR
        assert results[2].category_result.category == CarCategory.COMPLEX_BUT_PROFITABLE

    @pytest.mark.asyncio
    async def test_with_price_scoring(self) -> None:
        response_data = [
            {
                "id": "1",
                "category": "complex_but_profitable",
                "confidence": 0.85,
                "flags": ["срочно", "торг"],
                "reasoning": "Цена ниже рынка",
            },
        ]

        processor = BatchProcessor(api_key="test-key")
        listings = [
            ListingDescription(
                id="1",
                text="Срочно продам, торг",
                price=300_000,
                market_price=500_000,
            ),
        ]

        with patch.object(
            processor,
            "_call_api_with_retry",
            new_callable=AsyncMock,
            return_value=_make_batch_response(response_data),
        ):
            results = await processor.process(listings)

        assert len(results) == 1
        assert results[0].price_ratio == 0.6
        assert results[0].attractiveness_score > 5.0
