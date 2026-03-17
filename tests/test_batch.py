"""Тесты batch processing."""

import json
from unittest.mock import MagicMock, patch

from perekup_helper.batch import BatchProcessor
from perekup_helper.models import CarCategory, ListingDescription


def _make_batch_response(items: list[dict[str, object]]) -> str:
    return json.dumps(items, ensure_ascii=False)


class TestBatchProcessor:
    @patch("perekup_helper.batch.anthropic.Anthropic")
    @patch("perekup_helper.categorizer.anthropic.Anthropic")
    def test_single_batch(self, mock_cat_cls: MagicMock, mock_batch_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_batch_cls.return_value = mock_client

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
                "category": "junk",
                "confidence": 0.8,
                "flags": ["не на ходу"],
                "reasoning": "Хлам",
            },
        ]
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text=_make_batch_response(response_data))]
        mock_client.messages.create.return_value = mock_message

        processor = BatchProcessor(api_key="test-key", batch_size=5)
        listings = [
            ListingDescription(id="1", text="Один хозяин, гараж"),
            ListingDescription(id="2", text="Не на ходу, на запчасти"),
        ]

        results = processor.process(listings)

        assert len(results) == 2
        assert results[0].listing_id == "1"
        assert results[0].category_result.category == CarCategory.CLEAN
        assert results[1].listing_id == "2"
        assert results[1].category_result.category == CarCategory.JUNK
        # Один API-вызов на весь батч
        mock_client.messages.create.assert_called_once()

    @patch("perekup_helper.batch.anthropic.Anthropic")
    @patch("perekup_helper.categorizer.anthropic.Anthropic")
    def test_multiple_batches(self, mock_cat_cls: MagicMock, mock_batch_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_batch_cls.return_value = mock_client

        # Два батча по 2 объявления
        batch1 = [
            {"id": "1", "category": "clean", "confidence": 0.9, "flags": [], "reasoning": "ok"},
            {"id": "2", "category": "damaged_body", "confidence": 0.7, "flags": [], "reasoning": "ok"},
        ]
        batch2 = [
            {"id": "3", "category": "owner_debtor", "confidence": 0.8, "flags": [], "reasoning": "ok"},
        ]
        mock_client.messages.create.side_effect = [
            MagicMock(content=[MagicMock(text=_make_batch_response(batch1))]),
            MagicMock(content=[MagicMock(text=_make_batch_response(batch2))]),
        ]

        processor = BatchProcessor(api_key="test-key", batch_size=2, rate_limit_delay=0.0)
        listings = [
            ListingDescription(id="1", text="A"),
            ListingDescription(id="2", text="B"),
            ListingDescription(id="3", text="C"),
        ]

        results = processor.process(listings)

        assert len(results) == 3
        assert results[2].category_result.category == CarCategory.OWNER_DEBTOR
        assert mock_client.messages.create.call_count == 2

    @patch("anthropic.Anthropic")
    def test_fallback_on_batch_error(self, mock_anthropic_cls: MagicMock) -> None:
        """При ошибке в батче — fallback на поштучную обработку."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        single_response = json.dumps(
            {
                "category": "document_issues",
                "confidence": 0.7,
                "flags": ["без ПТС"],
                "reasoning": "Нет документов",
            }
        )

        # Первый вызов (batch) — невалидный JSON, второй (single fallback) — валидный
        mock_client.messages.create.side_effect = [
            MagicMock(content=[MagicMock(text="INVALID JSON")]),
            MagicMock(content=[MagicMock(text=single_response)]),
        ]

        processor = BatchProcessor(api_key="test-key", batch_size=5, rate_limit_delay=0.0)
        listings = [
            ListingDescription(id="1", text="Без ПТС"),
        ]

        results = processor.process(listings)

        assert len(results) == 1
        assert results[0].category_result.category == CarCategory.DOCUMENT_ISSUES

    @patch("perekup_helper.batch.anthropic.Anthropic")
    @patch("perekup_helper.categorizer.anthropic.Anthropic")
    def test_with_price_scoring(self, mock_cat_cls: MagicMock, mock_batch_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_batch_cls.return_value = mock_client

        response_data = [
            {
                "id": "1",
                "category": "complex_profitable",
                "confidence": 0.85,
                "flags": ["срочно", "торг"],
                "reasoning": "Цена ниже рынка",
            },
        ]
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text=_make_batch_response(response_data))]
        )

        processor = BatchProcessor(api_key="test-key")
        listings = [
            ListingDescription(
                id="1",
                text="Срочно продам, торг",
                price=300_000,
                market_price=500_000,
            ),
        ]

        results = processor.process(listings)

        assert len(results) == 1
        assert results[0].price_ratio == 0.6
        assert results[0].attractiveness_score > 5.0
