"""Тесты моделей данных."""

import pytest

from perekup_helper.models import (
    CATEGORY_BASE_SCORES,
    CATEGORY_LABELS,
    CarCategory,
    CategoryResult,
    ListingDescription,
    ScoreResult,
)


class TestCarCategory:
    def test_all_categories_have_labels(self) -> None:
        for cat in CarCategory:
            assert cat in CATEGORY_LABELS

    def test_all_categories_have_scores(self) -> None:
        for cat in CarCategory:
            assert cat in CATEGORY_BASE_SCORES

    def test_category_values(self) -> None:
        assert CarCategory.CLEAN.value == "clean"
        assert CarCategory.JUNK.value == "junk"
        assert CarCategory.COMPLEX_PROFITABLE.value == "complex_profitable"

    def test_category_from_string(self) -> None:
        assert CarCategory("clean") == CarCategory.CLEAN
        assert CarCategory("damaged_body") == CarCategory.DAMAGED_BODY


class TestListingDescription:
    def test_minimal(self) -> None:
        listing = ListingDescription(id="1", text="Продам авто")
        assert listing.id == "1"
        assert listing.price is None
        assert listing.market_price is None

    def test_full(self) -> None:
        listing = ListingDescription(
            id="abc",
            text="Продам ВАЗ 2114",
            price=150_000,
            market_price=200_000,
        )
        assert listing.price == 150_000
        assert listing.market_price == 200_000


class TestCategoryResult:
    def test_valid(self) -> None:
        result = CategoryResult(
            category=CarCategory.CLEAN,
            confidence=0.9,
            flags=["один хозяин"],
            reasoning="Чистое объявление",
        )
        assert result.category == CarCategory.CLEAN
        assert result.confidence == 0.9

    def test_confidence_bounds(self) -> None:
        with pytest.raises(Exception):
            CategoryResult(
                category=CarCategory.CLEAN,
                confidence=1.5,
                flags=[],
                reasoning="",
            )

    def test_empty_flags(self) -> None:
        result = CategoryResult(
            category=CarCategory.JUNK,
            confidence=0.5,
            reasoning="Мусор",
        )
        assert result.flags == []


class TestScoreResult:
    def test_full(self) -> None:
        cat = CategoryResult(
            category=CarCategory.CLEAN,
            confidence=0.95,
            flags=[],
            reasoning="ok",
        )
        score = ScoreResult(
            listing_id="1",
            category_result=cat,
            price_ratio=0.8,
            attractiveness_score=8.5,
        )
        assert score.listing_id == "1"
        assert score.price_ratio == 0.8
        assert 0 <= score.attractiveness_score <= 10

    def test_no_price_ratio(self) -> None:
        cat = CategoryResult(
            category=CarCategory.DAMAGED_BODY,
            confidence=0.6,
            flags=["после ДТП"],
            reasoning="Битый",
        )
        score = ScoreResult(
            listing_id="2",
            category_result=cat,
            attractiveness_score=3.0,
        )
        assert score.price_ratio is None
