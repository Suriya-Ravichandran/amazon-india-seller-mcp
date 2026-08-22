"""Tests for the calculate_profitability tool and the pricing service."""

from __future__ import annotations

import pytest

from amazon_india_seller_mcp.config.settings import Settings
from amazon_india_seller_mcp.services.pricing_service import PricingService
from tests import build_services, run
from amazon_india_seller_mcp.tools.profit_calculator import calculate_profitability


@pytest.fixture(scope="module")
def services():
    return build_services()


@pytest.fixture(scope="module")
def pricing():
    return PricingService(Settings())


@pytest.fixture(scope="module")
def report(services):
    return run(
        calculate_profitability(
            services,
            selling_price=399,
            product_cost=120,
            packaging_cost=15,
            weight_grams=250,
            fulfillment_method="FBA",
            category="Home & Kitchen",
            expected_return_rate=0.05,
            other_costs=10,
        )
    )


def test_profit_report_has_all_required_fields(report):
    assert report["ok"] is True
    for field in (
        "selling_price", "product_cost", "packaging_cost", "amazon_fees",
        "shipping_or_fulfillment_cost", "return_reserve", "other_costs", "total_cost",
        "estimated_profit_per_order", "profit_margin", "roi", "break_even_price",
        "recommended_selling_price",
    ):
        assert field in report


def test_profit_equals_price_minus_total_cost(report):
    assert report["estimated_profit_per_order"] == pytest.approx(
        report["selling_price"] - report["total_cost"], abs=0.02
    )


def test_total_cost_is_the_sum_of_its_parts(report):
    parts = (
        report["product_cost"] + report["packaging_cost"] + report["amazon_fees"]
        + report["shipping_or_fulfillment_cost"] + report["return_reserve"] + report["other_costs"]
    )
    assert report["total_cost"] == pytest.approx(parts, abs=0.02)


def test_margin_and_roi_are_consistent(report):
    assert report["profit_margin"] == pytest.approx(
        report["estimated_profit_per_order"] / report["selling_price"], abs=0.001
    )
    invested = report["product_cost"] + report["packaging_cost"] + report["other_costs"]
    assert report["roi"] == pytest.approx(report["estimated_profit_per_order"] / invested, abs=0.001)


def test_every_fee_line_carries_full_provenance(report):
    assert report["fee_breakdown"]
    for line in report["fee_breakdown"]:
        for field in ("fee_type", "amount", "source", "effective_date", "data_type"):
            assert line[field] not in (None, "")
        assert line["data_type"] in {"Verified", "Estimated", "Demo"}


def test_break_even_price_yields_zero_profit(services, pricing, report):
    at_break_even = pricing.calculate(
        selling_price=report["break_even_price"],
        product_cost=120,
        packaging_cost=15,
        weight_grams=250,
        fulfillment_method="FBA",
        category="Home & Kitchen",
        expected_return_rate=0.05,
        other_costs=10,
    )
    assert at_break_even.estimated_profit == pytest.approx(0.0, abs=1.0)


def test_recommended_price_hits_the_target_margin(pricing):
    breakdown = pricing.calculate(
        selling_price=399, product_cost=120, packaging_cost=15, weight_grams=250,
        fulfillment_method="FBA", category="Home & Kitchen", expected_return_rate=0.05, other_costs=10,
    )
    at_recommended = pricing.calculate(
        selling_price=breakdown.recommended_selling_price,
        product_cost=120, packaging_cost=15, weight_grams=250,
        fulfillment_method="FBA", category="Home & Kitchen", expected_return_rate=0.05, other_costs=10,
    )
    assert at_recommended.profit_margin == pytest.approx(0.30, abs=0.02)


@pytest.mark.parametrize("method", ["FBA", "Easy Ship", "Self Ship"])
def test_all_fulfilment_methods_are_supported(services, method):
    report = run(
        calculate_profitability(
            services, selling_price=499, product_cost=150, packaging_cost=15,
            weight_grams=300, fulfillment_method=method,
        )
    )
    assert report["ok"] is True
    assert report["fulfillment_method"] == method
    assert report["shipping_or_fulfillment_cost"] > 0


def test_heavier_products_cost_more_to_fulfil(services):
    light = run(calculate_profitability(services, selling_price=499, product_cost=150, weight_grams=200))
    heavy = run(calculate_profitability(services, selling_price=499, product_cost=150, weight_grams=900))
    assert heavy["shipping_or_fulfillment_cost"] > light["shipping_or_fulfillment_cost"]


def test_higher_return_rate_increases_the_reserve_and_cuts_profit(services):
    low = run(calculate_profitability(services, selling_price=399, product_cost=120, expected_return_rate=0.02))
    high = run(calculate_profitability(services, selling_price=399, product_cost=120, expected_return_rate=0.25))
    assert high["return_reserve"] > low["return_reserve"]
    assert high["estimated_profit_per_order"] < low["estimated_profit_per_order"]


def test_loss_making_product_is_reported_as_unprofitable(services):
    report = run(calculate_profitability(services, selling_price=199, product_cost=180, packaging_cost=20))
    assert report["estimated_profit_per_order"] < 0
    assert report["beginner_explanation"]["is_this_product_profitable"].startswith("No")
    assert "Do not launch" in report["beginner_explanation"]["should_the_seller_launch_it"]


def test_beginner_explanation_answers_all_four_questions(report):
    explanation = report["beginner_explanation"]
    for field in (
        "is_this_product_profitable", "should_the_seller_launch_it",
        "biggest_cost", "margin_safety_available",
    ):
        assert explanation[field]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"selling_price": 0, "product_cost": 100},
        {"selling_price": -399, "product_cost": 100},
        {"selling_price": 399, "product_cost": -1},
        {"selling_price": 399, "product_cost": 100, "weight_grams": 0},
        {"selling_price": 399, "product_cost": 100, "weight_grams": -250},
        {"selling_price": 399, "product_cost": 100, "expected_return_rate": 1.5},
        {"selling_price": 399, "product_cost": 100, "fulfillment_method": "Teleport"},
    ],
)
def test_invalid_inputs_return_clean_errors(services, kwargs):
    report = run(calculate_profitability(services, **kwargs))
    assert report["ok"] is False
    assert report["error"]["code"] == "invalid_input"
    assert "Traceback" not in report["error"]["message"]
    assert report["error"]["remediation"]


def test_fee_schedule_is_configurable_and_not_hardcoded(pricing):
    """Changing the schedule must change the calculated fees."""
    baseline = pricing.calculate(selling_price=399, product_cost=120, weight_grams=250)
    custom = PricingService(Settings())
    custom.fees = custom.fees.model_copy(update={"referral_fee_rates": {"default": 0.02}})
    cheaper = custom.calculate(selling_price=399, product_cost=120, weight_grams=250, category="Unknown Category")
    assert cheaper.amazon_fees < baseline.amazon_fees


def test_investment_planning_is_present(report):
    planning = report["investment_planning"]
    assert planning["units_for_20000_investment"] > planning["units_for_5000_investment"]
    assert "not guaranteed" in planning["caveat"].lower() or "never guaranteed" in planning["caveat"].lower()
