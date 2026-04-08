"""Tests for app.services.deal_scorer.compute_deal_score()."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.deal_scorer import compute_deal_score


def _make_listing(
    *,
    price_diff_pct: float | None = None,
    category: str | None = None,
    mileage: int | None = None,
    year: int | None = None,
    photo_count: int | None = None,
    created_at=None,
    owners_count: int | None = None,
) -> SimpleNamespace:
    """Build a lightweight listing stub for compute_deal_score().

    Uses SimpleNamespace so we can set arbitrary attributes without a real
    SQLAlchemy model or database.
    """
    analysis = None
    if category is not None:
        analysis = SimpleNamespace(category=category)

    return SimpleNamespace(
        price_diff_pct=price_diff_pct,
        analysis=analysis,
        mileage=mileage,
        year=year,
        photo_count=photo_count,
        created_at=created_at,
        owners_count=owners_count,
        description="",
    )


# ── Neutral baseline ────────────────────────────────────────────────────────


class TestNeutralScore:
    """No market price, no analysis -- should return the baseline of 50."""

    async def test_bare_listing_returns_50(self):
        listing = _make_listing()
        assert await compute_deal_score(listing) == 50

    async def test_no_analysis_no_price_diff(self):
        listing = _make_listing(price_diff_pct=None, category=None)
        assert await compute_deal_score(listing) == 50


# ── High score (great deal) ─────────────────────────────────────────────────


class TestHighScore:
    """Price 30% below market + clean category should yield a high score."""

    async def test_price_30pct_below_market_clean(self):
        # +30*2 = +60 (price) + 10 (clean) = 120 → clamped to 100
        listing = _make_listing(price_diff_pct=30.0, category="clean")
        score = await compute_deal_score(listing)
        assert score == 100

    async def test_price_15pct_below_market_clean(self):
        # 50 + 15*2 + 10 = 90
        listing = _make_listing(price_diff_pct=15.0, category="clean")
        score = await compute_deal_score(listing)
        assert score == 90

    async def test_price_15pct_below_clean_with_photos(self):
        # 50 + 15*2 + 10 + 3 (photos) = 93
        listing = _make_listing(price_diff_pct=15.0, category="clean", photo_count=10)
        score = await compute_deal_score(listing)
        assert score == 93


# ── Low score (bad deal / damaged) ──────────────────────────────────────────


class TestLowScore:
    """damaged_body category should heavily penalize the score."""

    async def test_damaged_body_at_market_price(self):
        # damaged_body: hard cap at 15 regardless of price
        listing = _make_listing(price_diff_pct=0.0, category="damaged_body")
        score = await compute_deal_score(listing)
        assert score == 15

    async def test_damaged_body_above_market(self):
        # damaged_body: hard cap at 15, then capped further by negative diff
        listing = _make_listing(price_diff_pct=-10.0, category="damaged_body")
        score = await compute_deal_score(listing)
        assert score == 15  # cap applies before negative diff adjustment

    async def test_bad_docs_penalty(self):
        # bad_docs: hard cap at 10
        listing = _make_listing(category="bad_docs")
        score = await compute_deal_score(listing)
        assert score == 10

    async def test_debtor_penalty(self):
        # debtor: hard cap at 10
        listing = _make_listing(category="debtor")
        score = await compute_deal_score(listing)
        assert score == 10

    async def test_score_floor_is_zero(self):
        # 50 + (-30)*2 - 30 (bad_docs) = 50 - 60 - 30 = -40 → clamped to 0
        listing = _make_listing(price_diff_pct=-30.0, category="bad_docs")
        score = await compute_deal_score(listing)
        assert score == 0


# ── Mileage bonus / penalty ─────────────────────────────────────────────────


class TestMileageEffect:
    """Low mileage per year → +5 bonus, high mileage per year → -5 penalty."""

    async def test_low_mileage_bonus(self):
        # Car: year 2022, mileage 15_000 → age 4 → 3750 km/yr < 10000 → +5
        # 50 + 5 = 55
        listing = _make_listing(year=2022, mileage=15_000)
        score = await compute_deal_score(listing)
        assert score == 55

    async def test_high_mileage_penalty(self):
        # Car: year 2020, mileage 200_000 → age 6 → 33333 km/yr > 30000 → -5
        # 50 - 5 = 45
        listing = _make_listing(year=2020, mileage=200_000)
        score = await compute_deal_score(listing)
        assert score == 45

    async def test_normal_mileage_no_effect(self):
        # Car: year 2020, mileage 100_000 → age 6 → 16666 km/yr (10k-30k) → 0
        # 50
        listing = _make_listing(year=2020, mileage=100_000)
        score = await compute_deal_score(listing)
        assert score == 50

    async def test_missing_mileage_no_effect(self):
        listing = _make_listing(year=2020, mileage=None)
        score = await compute_deal_score(listing)
        assert score == 50

    async def test_missing_year_no_effect(self):
        listing = _make_listing(year=None, mileage=50_000)
        score = await compute_deal_score(listing)
        assert score == 50
