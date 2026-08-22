"""Tests for revenue estimation, competitor classification and evergreen scoring."""

from __future__ import annotations

import pytest

from config.settings import Settings
from services.revenue_service import RevenueService
from tests import build_services, run
from tools.competitor_analysis import analyze_competitors
from tools.evergreen_analysis import analyze_evergreen
from tools.purchase_signals import analyze_purchase_signals
from tools.revenue_calculator import calculate_revenue


@pytest.fixture(scope="module")
def services():
    return build_services()


@pytest.fixture(scope="module")
def revenue():
    return RevenueService(Settings())


# -- units and revenue ------------------------------------------------------ #
def test_bsr_curve_is_monotonic(revenue):
    """A worse rank must never imply more sales."""
    ranks = [100, 1_000, 10_000, 50_000]
    units = [revenue.units_from_bsr(rank, "Home & Kitchen").units_per_month for rank in ranks]
    assert units == sorted(units, reverse=True)
    assert all(value >= 1 for value in units)


def test_bsr_estimate_returns_a_range_not_false_precision(revenue):
    estimate = revenue.units_from_bsr(5_000, "Home & Kitchen")
    assert estimate.range_low < estimate.units_per_month < estimate.range_high
    assert estimate.confidence == "Low"
    assert estimate.method == "bsr_curve"


def test_missing_bsr_yields_no_estimate(revenue):
    assert revenue.units_from_bsr(None, "Home & Kitchen") is None
    assert revenue.units_from_bsr(0, "Home & Kitchen") is None


def test_bought_badge_beats_the_bsr_curve(revenue):
    """Amazon's own published figure must win over the model."""
    estimate = revenue.best_units_estimate(bought_past_month=500, bsr=5_000, category="Home & Kitchen")
    assert estimate.method == "bought_in_past_month_badge"
    assert estimate.units_per_month == 500
    assert estimate.confidence == "High"


def test_revenue_maths(services):
    report = run(calculate_revenue(services, price=399, units_per_month=300))
    assert report["ok"] is True
    assert report["gross_monthly_revenue"] == pytest.approx(399 * 300, abs=0.01)
    assert report["gross_annual_revenue"] == pytest.approx(399 * 300 * 12, abs=0.01)


def test_revenue_with_product_cost_adds_profit(services):
    report = run(calculate_revenue(services, price=399, units_per_month=100, product_cost=120))
    assert report["estimated_monthly_profit"] is not None
    assert report["unit_economics"]["profit_per_unit"] > 0
    assert report["estimated_monthly_profit"] < report["gross_monthly_revenue"]


def test_revenue_requires_some_volume_signal(services):
    report = run(calculate_revenue(services, price=399))
    assert report["ok"] is False
    assert report["error"]["code"] == "invalid_input"


@pytest.mark.parametrize("price", [0, -10])
def test_invalid_price_rejected(services, price):
    report = run(calculate_revenue(services, price=price, units_per_month=10))
    assert report["ok"] is False


# -- competitor classification ---------------------------------------------- #
def test_new_seller_detection_uses_the_review_threshold(revenue):
    threshold = revenue.settings.new_seller_review_threshold
    new = revenue.profile_competitor({"price": 399, "review_count": threshold - 1, "bsr": 4_000})
    established = revenue.profile_competitor({"price": 399, "review_count": threshold * 100, "bsr": 4_000})
    assert new.is_new_seller is True
    assert new.seller_stage == "New Seller"
    assert established.is_new_seller is False
    assert established.seller_stage in {"Established", "Dominant"}


def test_volume_target_flag(revenue):
    target = revenue.settings.min_monthly_units_target
    assert target == 300
    strong = revenue.profile_competitor({"price": 399, "review_count": 10, "bought_past_month": target + 50})
    weak = revenue.profile_competitor({"price": 399, "review_count": 10, "bought_past_month": 20})
    assert strong.meets_volume_target is True
    assert weak.meets_volume_target is False


def test_new_seller_succeeding_is_called_out(revenue):
    profile = revenue.profile_competitor({"price": 399, "review_count": 5, "bought_past_month": 1_000})
    assert profile.is_new_seller and profile.meets_volume_target
    assert any("newcomer" in note for note in profile.notes)


def test_market_shares_sum_to_about_100(revenue):
    rows = [
        {"price": 300, "review_count": 100, "bought_past_month": 500},
        {"price": 400, "review_count": 900, "bought_past_month": 200},
        {"price": 250, "review_count": 20, "bought_past_month": 100},
    ]
    analysis = revenue.analyze_competitor_field(rows)
    total = sum(row["market_share_percent"] for row in analysis["competitors"])
    assert total == pytest.approx(100.0, abs=0.5)
    assert analysis["estimated_market_size_monthly_revenue"] > 0


def test_competitor_analysis_tool_end_to_end(services):
    report = run(analyze_competitors(services, "cable organizer", max_competitors=15))
    assert report["ok"] is True
    for field in ("new_sellers", "volume_target", "market_concentration", "entry_verdict", "competitors"):
        assert field in report
    assert report["competitors_analysed"] == 15
    assert report["data_type"] in {"Estimated", "Demo", "Live"}


def test_competitor_analysis_rejects_bad_input(services):
    report = run(analyze_competitors(services, "x"))
    assert report["ok"] is False
    assert report["error"]["code"] == "invalid_input"


def test_purchase_signals_never_treat_a_missing_badge_as_zero(services):
    report = run(analyze_purchase_signals(services, "soap dispenser"))
    assert report["ok"] is True
    assert report["listings_with_purchase_badge"] <= report["listings_checked"]
    assert any("does NOT mean no sales" in line for line in report["how_to_read_this"])


# -- evergreen -------------------------------------------------------------- #
def test_flat_series_scores_as_evergreen(revenue):
    result = revenue.evergreen_analysis("steady product", [50] * 36)
    assert result["is_evergreen"] is True
    assert result["verdict"] in {"Evergreen", "Mostly Evergreen"}
    assert result["evergreen_score"] > 70


def test_spiky_series_scores_as_seasonal(revenue):
    spiky = ([2] * 10 + [100] * 2) * 3
    result = revenue.evergreen_analysis("seasonal product", spiky)
    assert result["is_evergreen"] is False
    assert result["evergreen_score"] < result_of_flat(revenue)


def result_of_flat(revenue) -> float:
    return revenue.evergreen_analysis("steady", [50] * 36)["evergreen_score"]


def test_evergreen_needs_enough_data(revenue):
    with pytest.raises(Exception) as excinfo:
        revenue.evergreen_analysis("too short", [10, 20, 30])
    assert "at least 6" in str(excinfo.value)


def test_evergreen_tool_falls_back_without_network(services):
    """With Google Trends off, the tool must still work and say the data is modelled."""
    report = run(analyze_evergreen(services, "silicone sink strainer", years="3y"))
    assert report["ok"] is True
    assert report["data_type"] in {"Demo", "Estimated"}
    assert report["upgrade_hint"]
    assert 0 <= report["evergreen_score"] <= 100
    assert report["beginner_guidance"]
