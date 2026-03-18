"""Tests for AI analysis integration after parsing (avito_parser/analysis.py)."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.listing import Listing, ListingAnalysis  # noqa: F401 — register tables
from avito_parser.analysis import analyze_and_save
from perekup_helper.models import CarCategory, CategoryResult, ScoreResult


def _make_score_result(listing_id: str, category: str = "clean") -> ScoreResult:
    return ScoreResult(
        listing_id=listing_id,
        category_result=CategoryResult(
            category=CarCategory(category),
            confidence=0.9,
            flags=["один хозяин"],
            reasoning="Чистый автомобиль, без нареканий",
        ),
        price_ratio=0.85,
        attractiveness_score=7.5,
    )


@pytest.fixture
async def analysis_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
def sample_listing():
    return Listing(
        id=uuid.uuid4(),
        source="avito",
        external_id="test-ext-1",
        brand="Toyota",
        model="Camry",
        year=2020,
        price=1_500_000,
        market_price=1_700_000,
        price_diff_pct=-11.8,
        description="Один владелец, отличное состояние",
        url="https://avito.ru/test-ext-1",
    )


class TestAnalyzeAndSave:
    @patch("avito_parser.analysis.Categorizer")
    async def test_creates_analysis_for_new_listing(
        self, mock_cat_cls: MagicMock, analysis_session: AsyncSession, sample_listing: Listing
    ) -> None:
        listing_id = str(sample_listing.id)
        mock_cat = MagicMock()
        mock_cat_cls.return_value = mock_cat
        mock_cat.categorize_and_score.return_value = _make_score_result(listing_id)

        analysis_session.add(sample_listing)
        await analysis_session.flush()

        result = await analyze_and_save(analysis_session, sample_listing)

        assert result is not None
        assert result.listing_id == sample_listing.id
        assert result.category == "clean"
        assert float(result.confidence) == pytest.approx(0.9)
        assert result.score == pytest.approx(7.5)
        assert result.ai_summary == "Чистый автомобиль, без нареканий"
        assert result.flags == ["один хозяин"]

    @patch("avito_parser.analysis.Categorizer")
    async def test_saves_analysis_to_db(
        self, mock_cat_cls: MagicMock, analysis_session: AsyncSession, sample_listing: Listing
    ) -> None:
        listing_id = str(sample_listing.id)
        mock_cat = MagicMock()
        mock_cat_cls.return_value = mock_cat
        mock_cat.categorize_and_score.return_value = _make_score_result(listing_id)

        analysis_session.add(sample_listing)
        await analysis_session.flush()
        await analyze_and_save(analysis_session, sample_listing)
        await analysis_session.flush()

        count = await analysis_session.scalar(
            select(func.count()).select_from(ListingAnalysis).where(ListingAnalysis.listing_id == sample_listing.id)
        )
        assert count == 1

    @patch("avito_parser.analysis.Categorizer")
    async def test_skips_existing_analysis(
        self, mock_cat_cls: MagicMock, analysis_session: AsyncSession, sample_listing: Listing
    ) -> None:
        mock_cat = MagicMock()
        mock_cat_cls.return_value = mock_cat

        analysis_session.add(sample_listing)
        await analysis_session.flush()

        # Pre-insert an analysis
        existing = ListingAnalysis(
            id=uuid.uuid4(),
            listing_id=sample_listing.id,
            category="clean",
            confidence=0.8,
            ai_summary="Уже есть",
        )
        analysis_session.add(existing)
        await analysis_session.flush()

        result = await analyze_and_save(analysis_session, sample_listing)

        assert result is None
        mock_cat.categorize_and_score.assert_not_called()

    @patch("avito_parser.analysis.Categorizer")
    async def test_no_duplicate_on_second_call(
        self, mock_cat_cls: MagicMock, analysis_session: AsyncSession, sample_listing: Listing
    ) -> None:
        listing_id = str(sample_listing.id)
        mock_cat = MagicMock()
        mock_cat_cls.return_value = mock_cat
        mock_cat.categorize_and_score.return_value = _make_score_result(listing_id)

        analysis_session.add(sample_listing)
        await analysis_session.flush()

        await analyze_and_save(analysis_session, sample_listing)
        await analysis_session.flush()

        # Second call — should be skipped
        result2 = await analyze_and_save(analysis_session, sample_listing)
        assert result2 is None

        count = await analysis_session.scalar(
            select(func.count()).select_from(ListingAnalysis).where(ListingAnalysis.listing_id == sample_listing.id)
        )
        assert count == 1

    @patch("avito_parser.analysis.Categorizer")
    async def test_handles_categorizer_error_gracefully(
        self, mock_cat_cls: MagicMock, analysis_session: AsyncSession, sample_listing: Listing
    ) -> None:
        mock_cat = MagicMock()
        mock_cat_cls.return_value = mock_cat
        mock_cat.categorize_and_score.side_effect = RuntimeError("Claude API timeout")

        analysis_session.add(sample_listing)
        await analysis_session.flush()

        result = await analyze_and_save(analysis_session, sample_listing)

        assert result is None

        count = await analysis_session.scalar(
            select(func.count()).select_from(ListingAnalysis).where(ListingAnalysis.listing_id == sample_listing.id)
        )
        assert count == 0

    @patch("avito_parser.analysis.Categorizer")
    async def test_uses_description_as_text(
        self, mock_cat_cls: MagicMock, analysis_session: AsyncSession, sample_listing: Listing
    ) -> None:
        listing_id = str(sample_listing.id)
        mock_cat = MagicMock()
        mock_cat_cls.return_value = mock_cat
        mock_cat.categorize_and_score.return_value = _make_score_result(listing_id)

        analysis_session.add(sample_listing)
        await analysis_session.flush()
        await analyze_and_save(analysis_session, sample_listing)

        call_args = mock_cat.categorize_and_score.call_args[0][0]
        assert call_args.text == sample_listing.description
        assert call_args.price == sample_listing.price
        assert call_args.market_price == sample_listing.market_price

    @patch("avito_parser.analysis.Categorizer")
    async def test_falls_back_to_brand_model_year_when_no_description(
        self, mock_cat_cls: MagicMock, analysis_session: AsyncSession
    ) -> None:
        listing = Listing(
            id=uuid.uuid4(),
            source="avito",
            external_id="no-desc",
            brand="BMW",
            model="X5",
            year=2021,
            price=3_000_000,
            market_price=None,
            description=None,
            url="https://avito.ru/no-desc",
        )
        listing_id = str(listing.id)
        mock_cat = MagicMock()
        mock_cat_cls.return_value = mock_cat
        mock_cat.categorize_and_score.return_value = _make_score_result(listing_id, "clean")

        analysis_session.add(listing)
        await analysis_session.flush()
        await analyze_and_save(analysis_session, listing)

        call_args = mock_cat.categorize_and_score.call_args[0][0]
        assert "BMW" in call_args.text
        assert "X5" in call_args.text

    @patch("avito_parser.analysis.Categorizer")
    async def test_stores_all_car_categories(self, mock_cat_cls: MagicMock, analysis_session: AsyncSession) -> None:
        """All CarCategory values can be stored in listing_analysis.category."""
        for cat in CarCategory:
            listing = Listing(
                id=uuid.uuid4(),
                source="avito",
                external_id=f"cat-test-{cat.value}",
                brand="Test",
                model="Car",
                year=2020,
                price=500_000,
                market_price=600_000,
                description=f"Тест категории {cat.value}",
                url=f"https://avito.ru/{cat.value}",
            )
            mock_cat = MagicMock()
            mock_cat_cls.return_value = mock_cat
            mock_cat.categorize_and_score.return_value = _make_score_result(str(listing.id), cat.value)

            analysis_session.add(listing)
            await analysis_session.flush()
            result = await analyze_and_save(analysis_session, listing)
            await analysis_session.flush()

            assert result is not None
            assert result.category == cat.value
