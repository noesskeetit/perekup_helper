"""Tests for app.services.deal_scorer.compute_deal_score()."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.deal_scorer import compute_deal_score


def _make_listing(
    *,
    price: int = 500_000,
    price_diff_pct: float | None = None,
    category: str | None = None,
    mileage: int | None = None,
    year: int | None = None,
    photo_count: int | None = None,
    created_at=None,
    owners_count: int | None = None,
    body_type: str | None = "sedan",
) -> SimpleNamespace:
    """Build a lightweight listing stub for compute_deal_score().

    Uses SimpleNamespace so we can set arbitrary attributes without a real
    SQLAlchemy model or database.
    """
    analysis = None
    if category is not None:
        analysis = SimpleNamespace(category=category)

    return SimpleNamespace(
        price=price,
        price_diff_pct=price_diff_pct,
        analysis=analysis,
        mileage=mileage,
        year=year,
        photo_count=photo_count,
        created_at=created_at,
        owners_count=owners_count,
        body_type=body_type,
        description="",
    )


# ── Neutral baseline ────────────────────────────────────────────────────────


class TestNeutralScore:
    """No market price, no analysis -- baseline minus missing-data penalty."""

    async def test_bare_listing_with_body_type(self):
        # Has body_type but missing mileage and year → -8 (2 missing fields)
        listing = _make_listing()
        assert await compute_deal_score(listing) == 42

    async def test_complete_listing_returns_50(self):
        # mileage 90K / year 2020 → 15K km/yr → neutral range (10-30K)
        listing = _make_listing(mileage=90_000, year=2020)
        assert await compute_deal_score(listing) == 50

    async def test_no_analysis_no_price_diff(self):
        listing = _make_listing(price_diff_pct=None, category=None, mileage=90_000, year=2020)
        assert await compute_deal_score(listing) == 50


# ── High score (great deal) ─────────────────────────────────────────────────


class TestHighScore:
    """Price below market + clean category should yield a high score."""

    async def test_price_30pct_below_market_clean(self):
        # Complete: 50 + 30*1.5 + 10 (clean) = 105 → 100
        listing = _make_listing(price_diff_pct=30.0, category="clean", mileage=80_000, year=2020)
        score = await compute_deal_score(listing)
        assert score == 100

    async def test_price_30pct_below_incomplete_data_capped(self):
        # Incomplete (no mileage, no year = 2 missing): capped at 65
        listing = _make_listing(price_diff_pct=30.0, category="clean")
        score = await compute_deal_score(listing)
        assert score == 65

    async def test_price_15pct_below_market_clean_complete(self):
        # 50 + 15*1.5 + 10 (clean) = 82 (complete listing)
        listing = _make_listing(price_diff_pct=15.0, category="clean", mileage=80_000, year=2020)
        score = await compute_deal_score(listing)
        assert score == 82

    async def test_price_15pct_below_clean_with_photos(self):
        # 50 + 15*1.5 + 10 + 5 (photos>10) = 87
        listing = _make_listing(price_diff_pct=15.0, category="clean", photo_count=15, mileage=80_000, year=2020)
        score = await compute_deal_score(listing)
        assert score == 87

    async def test_extreme_diff_capped_at_30(self):
        # 80% diff capped to 30%: 50 + 30*1.5 + 10 = 105 → 100
        listing = _make_listing(price_diff_pct=80.0, category="clean", mileage=80_000, year=2020)
        score = await compute_deal_score(listing)
        assert score == 100

        # Same as 30% diff for complete listing
        listing2 = _make_listing(price_diff_pct=30.0, category="clean", mileage=80_000, year=2020)
        score2 = await compute_deal_score(listing2)
        assert score2 == 100


# ── Low score (bad deal / damaged) ──────────────────────────────────────────


class TestLowScore:
    """damaged_body category should heavily penalize the score."""

    async def test_damaged_body_at_market_price(self):
        # Hard cap at 15 regardless of accumulated score
        listing = _make_listing(price_diff_pct=0.0, category="damaged_body", mileage=80_000, year=2020)
        score = await compute_deal_score(listing)
        assert score == 15

    async def test_damaged_body_above_market(self):
        listing = _make_listing(price_diff_pct=-10.0, category="damaged_body", mileage=80_000, year=2020)
        score = await compute_deal_score(listing)
        assert score == 15

    async def test_bad_docs_penalty(self):
        # Hard cap at 10
        listing = _make_listing(category="bad_docs", mileage=80_000, year=2020)
        score = await compute_deal_score(listing)
        assert score == 10

    async def test_debtor_penalty(self):
        listing = _make_listing(category="debtor", mileage=80_000, year=2020)
        score = await compute_deal_score(listing)
        assert score == 10

    async def test_score_floor_is_zero(self):
        # 50 - 30*1.5 = 5, bad_docs cap = min(5, 10) = 5 → 5
        # Need even more negative to hit 0
        listing = _make_listing(price_diff_pct=-30.0, category="bad_docs", mileage=80_000, year=2020)
        score = await compute_deal_score(listing)
        assert score == 5

    async def test_very_overpriced_bad_docs_is_zero(self):
        # 50 - 30*1.5 - 5(cheap) - 5(high km) = -5 → clamped 0
        listing = _make_listing(price_diff_pct=-30.0, category="bad_docs", price=150_000, mileage=200_000, year=2020)
        score = await compute_deal_score(listing)
        assert score == 0

    async def test_cheap_listing_capped(self):
        # Price < 100K → hard cap at 20
        listing = _make_listing(price=30_000, price_diff_pct=40.0, mileage=80_000, year=2020)
        score = await compute_deal_score(listing)
        assert score == 20


# ── Age dampening ──────────────────────────────────────────────────────────


class TestAgeDampening:
    """Old cars get dampened price_diff bonus due to condition variance."""

    async def test_old_car_dampened(self):
        # year 2005, age=21 → >15 → diff * 0.5
        # 50 + (20 * 0.5 * 1.5) + 10 (clean) = 75
        # mileage 300K/21yr = 14.3K → neutral range
        listing = _make_listing(price_diff_pct=20.0, category="clean", year=2005, mileage=300_000)
        score = await compute_deal_score(listing)
        assert score == 75

    async def test_medium_old_car_dampened(self):
        # year 2014, age=12 → >10 → diff * 0.75
        # 50 + (20 * 0.75 * 1.5) + 10 (clean) = 82
        listing = _make_listing(price_diff_pct=20.0, category="clean", year=2014, mileage=150_000)
        score = await compute_deal_score(listing)
        assert score == 82

    async def test_new_car_no_dampening(self):
        # year 2020, age=6 → no dampening
        # 50 + (20 * 1.5) + 10 (clean) = 90
        listing = _make_listing(price_diff_pct=20.0, category="clean", year=2020, mileage=90_000)
        score = await compute_deal_score(listing)
        assert score == 90


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

    async def test_missing_mileage_penalty(self):
        # Missing mileage → 1 missing field → -3
        listing = _make_listing(year=2020, mileage=None)
        score = await compute_deal_score(listing)
        assert score == 47

    async def test_missing_year_penalty(self):
        # Missing year → 1 missing field → -3
        listing = _make_listing(year=None, mileage=50_000)
        score = await compute_deal_score(listing)
        assert score == 47
