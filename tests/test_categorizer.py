"""Тесты категоризатора (с мок OpenRouter API, async)."""

import json
from unittest.mock import AsyncMock, patch

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
    resolve_category,
)


def _make_response_json(
    category: str = "clean",
    confidence: float = 0.9,
    flags: list[str] | None = None,
    reasoning: str = "Тест",
) -> str:
    """Сформировать JSON-ответ как от LLM."""
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
        raw = _make_response_json("clean", 0.95, ["один хозяин"], "Всё ок")
        result = Categorizer._parse_response(raw)
        assert result.category == CarCategory.CLEAN
        assert result.confidence == 0.95
        assert "один хозяин" in result.flags

    def test_with_markdown_wrapper(self) -> None:
        raw = "```json\n" + _make_response_json("damaged_body", 0.8) + "\n```"
        result = Categorizer._parse_response(raw)
        assert result.category == CarCategory.DAMAGED_BODY

    def test_json_in_text(self) -> None:
        """JSON embedded in surrounding text should be extracted."""
        raw = "Вот мой анализ:\n" + _make_response_json("bad_docs", 0.7) + "\nНадеюсь помог!"
        result = Categorizer._parse_response(raw)
        assert result.category == CarCategory.BAD_DOCS

    def test_invalid_json_returns_fallback(self) -> None:
        result = Categorizer._parse_response("not a json at all")
        assert result.category == CarCategory.CLEAN
        assert result.confidence == 0.3
        assert result.reasoning == "parse error"

    def test_all_categories(self) -> None:
        for cat in CarCategory:
            raw = _make_response_json(cat.value, 0.7)
            result = Categorizer._parse_response(raw)
            assert result.category == cat

    def test_legacy_category_document_issues(self) -> None:
        """Legacy 'document_issues' should map to bad_docs via fuzzy matching."""
        raw = _make_response_json("document_issues", 0.8)
        result = Categorizer._parse_response(raw)
        assert result.category == CarCategory.BAD_DOCS

    def test_legacy_category_owner_debtor(self) -> None:
        """Legacy 'owner_debtor' should map to debtor."""
        raw = _make_response_json("owner_debtor", 0.9)
        result = Categorizer._parse_response(raw)
        assert result.category == CarCategory.DEBTOR

    def test_legacy_category_complex_profitable(self) -> None:
        """Legacy 'complex_profitable' should map to complex_but_profitable."""
        raw = _make_response_json("complex_profitable", 0.85)
        result = Categorizer._parse_response(raw)
        assert result.category == CarCategory.COMPLEX_BUT_PROFITABLE

    def test_legacy_category_junk(self) -> None:
        """Legacy 'junk' should map to damaged_body."""
        raw = _make_response_json("junk", 0.8)
        result = Categorizer._parse_response(raw)
        assert result.category == CarCategory.DAMAGED_BODY

    def test_unknown_category_falls_back(self) -> None:
        """Completely unknown category should fallback to CLEAN."""
        raw = _make_response_json("totally_unknown_xyz", 0.5)
        result = Categorizer._parse_response(raw)
        assert result.category == CarCategory.CLEAN

    def test_damaged_body_with_flags(self) -> None:
        raw = _make_response_json(
            "damaged_body",
            0.85,
            ["после ДТП", "требует вложений"],
            "Серьёзное ДТП, кузов под замену",
        )
        result = Categorizer._parse_response(raw)
        assert result.category == CarCategory.DAMAGED_BODY
        assert len(result.flags) == 2
        assert "после ДТП" in result.flags

    def test_debtor_category(self) -> None:
        raw = _make_response_json("debtor", 0.9, ["залог", "кредит"], "В залоге у банка")
        result = Categorizer._parse_response(raw)
        assert result.category == CarCategory.DEBTOR


class TestResolveCategory:
    def test_exact_enum_values(self) -> None:
        assert resolve_category("clean") == CarCategory.CLEAN
        assert resolve_category("damaged_body") == CarCategory.DAMAGED_BODY
        assert resolve_category("bad_docs") == CarCategory.BAD_DOCS
        assert resolve_category("debtor") == CarCategory.DEBTOR
        assert resolve_category("complex_but_profitable") == CarCategory.COMPLEX_BUT_PROFITABLE

    def test_legacy_aliases(self) -> None:
        assert resolve_category("document_issues") == CarCategory.BAD_DOCS
        assert resolve_category("owner_debtor") == CarCategory.DEBTOR
        assert resolve_category("complex_profitable") == CarCategory.COMPLEX_BUT_PROFITABLE
        assert resolve_category("junk") == CarCategory.DAMAGED_BODY

    def test_whitespace_handling(self) -> None:
        assert resolve_category("  clean  ") == CarCategory.CLEAN
        assert resolve_category("bad_docs\n") == CarCategory.BAD_DOCS

    def test_unknown_returns_clean(self) -> None:
        assert resolve_category("zzz_unknown") == CarCategory.CLEAN


class TestCategorizeAsync:
    @pytest.mark.asyncio
    async def test_categorize_single(self) -> None:
        cat = Categorizer(api_key="test-key")

        mock_response = _make_response_json("clean", 0.95, ["один хозяин"])
        with patch.object(cat, "_call_openrouter", new_callable=AsyncMock, return_value=mock_response):
            listing = ListingDescription(id="1", text="Продам авто, один хозяин")
            result = await cat.categorize(listing)

        assert result.category == CarCategory.CLEAN
        assert result.confidence == 0.95

    @pytest.mark.asyncio
    async def test_categorize_and_score(self) -> None:
        cat = Categorizer(api_key="test-key")

        mock_response = _make_response_json("complex_but_profitable", 0.8, ["срочно", "торг"])
        with patch.object(cat, "_call_openrouter", new_callable=AsyncMock, return_value=mock_response):
            listing = ListingDescription(
                id="2",
                text="Срочно продам, торг",
                price=300_000,
                market_price=500_000,
            )
            result = await cat.categorize_and_score(listing)

        assert result.listing_id == "2"
        assert result.category_result.category == CarCategory.COMPLEX_BUT_PROFITABLE
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

    def test_damaged_body_at_market(self) -> None:
        result = CategoryResult(
            category=CarCategory.DAMAGED_BODY,
            confidence=0.9,
            flags=[],
            reasoning="",
        )
        score = _compute_attractiveness(result, 1.0)
        assert score >= 0.0

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

    def test_complex_but_profitable_deep_discount(self) -> None:
        result = CategoryResult(
            category=CarCategory.COMPLEX_BUT_PROFITABLE,
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
                    assert 0.0 <= score <= 10.0, f"Score {score} out of bounds for {cat}, conf={conf}, ratio={ratio}"
