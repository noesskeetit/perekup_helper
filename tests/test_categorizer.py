"""Тесты категоризатора (с мок Claude API)."""

import json
from unittest.mock import MagicMock, patch

import pytest

from perekup_helper.categorizer import (
    Categorizer,
    _compute_attractiveness,
    _compute_price_ratio,
)
from perekup_helper.models import (
    CarCategory,
    CategoryResult,
    ListingDescription,
)


def _make_claude_response(
    category: str = "clean",
    confidence: float = 0.9,
    flags: list[str] | None = None,
    reasoning: str = "Тест",
) -> str:
    """Сформировать JSON-ответ как от Claude."""
    return json.dumps(
        {
            "category": category,
            "confidence": confidence,
            "flags": flags or [],
            "reasoning": reasoning,
        },
        ensure_ascii=False,
    )


class TestParseResponse:
    def test_valid_json(self) -> None:
        raw = _make_claude_response("clean", 0.95, ["один хозяин"], "Всё ок")
        result = Categorizer._parse_response(raw)
        assert result.category == CarCategory.CLEAN
        assert result.confidence == 0.95
        assert "один хозяин" in result.flags

    def test_with_markdown_wrapper(self) -> None:
        raw = "```json\n" + _make_claude_response("junk", 0.8) + "\n```"
        result = Categorizer._parse_response(raw)
        assert result.category == CarCategory.JUNK

    def test_invalid_json(self) -> None:
        with pytest.raises(ValueError, match="Невалидный JSON"):
            Categorizer._parse_response("not a json")

    def test_unknown_category(self) -> None:
        raw = _make_claude_response("unknown_cat", 0.5)
        with pytest.raises(ValueError, match="Неизвестная категория"):
            Categorizer._parse_response(raw)

    def test_all_categories(self) -> None:
        for cat in CarCategory:
            raw = _make_claude_response(cat.value, 0.7)
            result = Categorizer._parse_response(raw)
            assert result.category == cat

    def test_damaged_body_with_flags(self) -> None:
        raw = _make_claude_response(
            "damaged_body",
            0.85,
            ["после ДТП", "требует вложений"],
            "Серьёзное ДТП, кузов под замену",
        )
        result = Categorizer._parse_response(raw)
        assert result.category == CarCategory.DAMAGED_BODY
        assert len(result.flags) == 2
        assert "после ДТП" in result.flags

    def test_owner_debtor(self) -> None:
        raw = _make_claude_response(
            "owner_debtor", 0.9, ["залог", "кредит"], "В залоге у банка"
        )
        result = Categorizer._parse_response(raw)
        assert result.category == CarCategory.OWNER_DEBTOR


class TestCategorize:
    @patch("perekup_helper.categorizer.anthropic.Anthropic")
    def test_categorize_single(self, mock_anthropic_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        mock_message = MagicMock()
        mock_message.content = [
            MagicMock(text=_make_claude_response("clean", 0.95, ["один хозяин"]))
        ]
        mock_client.messages.create.return_value = mock_message

        cat = Categorizer(api_key="test-key")
        listing = ListingDescription(id="1", text="Продам авто, один хозяин")
        result = cat.categorize(listing)

        assert result.category == CarCategory.CLEAN
        assert result.confidence == 0.95
        mock_client.messages.create.assert_called_once()

    @patch("perekup_helper.categorizer.anthropic.Anthropic")
    def test_categorize_and_score(self, mock_anthropic_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        mock_message = MagicMock()
        mock_message.content = [
            MagicMock(
                text=_make_claude_response(
                    "complex_profitable", 0.8, ["срочно", "торг"]
                )
            )
        ]
        mock_client.messages.create.return_value = mock_message

        cat = Categorizer(api_key="test-key")
        listing = ListingDescription(
            id="2",
            text="Срочно продам, торг",
            price=300_000,
            market_price=500_000,
        )
        result = cat.categorize_and_score(listing)

        assert result.listing_id == "2"
        assert result.category_result.category == CarCategory.COMPLEX_PROFITABLE
        assert result.price_ratio == 0.6
        assert result.attractiveness_score > 0


class TestPriceRatio:
    def test_below_market(self) -> None:
        ratio = _compute_price_ratio(300_000, 500_000)
        assert ratio == 0.6

    def test_at_market(self) -> None:
        ratio = _compute_price_ratio(500_000, 500_000)
        assert ratio == 1.0

    def test_above_market(self) -> None:
        ratio = _compute_price_ratio(600_000, 500_000)
        assert ratio is not None
        assert ratio > 1.0

    def test_no_price(self) -> None:
        assert _compute_price_ratio(None, 500_000) is None
        assert _compute_price_ratio(500_000, None) is None
        assert _compute_price_ratio(None, None) is None

    def test_zero_market(self) -> None:
        assert _compute_price_ratio(100_000, 0) is None


class TestAttractiveness:
    def test_clean_below_market(self) -> None:
        result = CategoryResult(
            category=CarCategory.CLEAN,
            confidence=1.0,
            flags=[],
            reasoning="",
        )
        score = _compute_attractiveness(result, 0.6)
        # clean=5.0 + discount(0.4*2=0.8)*5=4.0 => 9.0 * (0.7+0.3*1.0) = 9.0
        assert score == 9.0

    def test_junk_at_market(self) -> None:
        result = CategoryResult(
            category=CarCategory.JUNK,
            confidence=0.9,
            flags=[],
            reasoning="",
        )
        score = _compute_attractiveness(result, 1.0)
        # junk=0.0 + discount(0.0)*5=0.0 => 0.0 * confidence_factor = 0.0
        assert score == 0.0

    def test_no_price_data(self) -> None:
        result = CategoryResult(
            category=CarCategory.CLEAN,
            confidence=0.8,
            flags=[],
            reasoning="",
        )
        score = _compute_attractiveness(result, None)
        # clean=5.0 + neutral=2.5 => 7.5 * (0.7+0.3*0.8) = 7.5*0.94 = 7.05 => 7.0
        assert 6.0 <= score <= 8.0

    def test_complex_profitable_deep_discount(self) -> None:
        result = CategoryResult(
            category=CarCategory.COMPLEX_PROFITABLE,
            confidence=0.85,
            flags=["срочно"],
            reasoning="",
        )
        score = _compute_attractiveness(result, 0.4)
        # complex=3.5 + discount(0.6*2=1.0, capped)*5=5.0 => 8.5 * (0.7+0.3*0.85)=8.5*0.955=8.1
        assert score > 7.0

    def test_score_bounds(self) -> None:
        """Скор всегда в диапазоне 0..10."""
        for cat in CarCategory:
            for conf in [0.0, 0.5, 1.0]:
                for ratio in [None, 0.1, 0.5, 1.0, 1.5]:
                    result = CategoryResult(
                        category=cat,
                        confidence=conf,
                        flags=[],
                        reasoning="",
                    )
                    score = _compute_attractiveness(result, ratio)
                    assert 0.0 <= score <= 10.0, (
                        f"Score {score} out of bounds for {cat}, conf={conf}, ratio={ratio}"
                    )
