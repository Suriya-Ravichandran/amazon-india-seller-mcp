"""Tests for the research_product tool, opportunity scoring and demo mode."""

from __future__ import annotations

import pytest

from amazon_india_seller_mcp.services import (
    OpportunityScores,
    recommendation_for_score,
    score_opportunity,
)
from tests import build_services, run
from amazon_india_seller_mcp.tools.product_research import research_product

PRODUCT = "silicone sink strainer"


@pytest.fixture(scope="module")
def services():
    return build_services()


@pytest.fixture(scope="module")
def report(services):
    return run(research_product(services, PRODUCT, "amazon.in"))


def test_research_product_returns_all_required_fields(report):
    assert report["ok"] is True
    for field in (
        "product_name", "category", "price_range", "average_price", "bsr",
        "estimated_weight_grams", "rating", "review_count", "estimated_demand",
        "demand_score", "competition_level", "return_risk", "gated_category_risk",
        "brand_approval_risk", "beginner_score", "overall_opportunity_score",
        "recommendation",
    ):
        assert field in report, f"missing field: {field}"


def test_opportunity_score_is_within_range_and_matches_recommendation(report):
    score = report["overall_opportunity_score"]
    assert 0 <= score <= 100
    assert report["recommendation"] == recommendation_for_score(score)


def test_every_result_carries_provenance(report):
    for field in ("source", "data_type", "confidence", "last_updated"):
        assert report[field], f"missing provenance field: {field}"
    assert report["data_type"] == "Demo"
    assert report["confidence"] == "Low"


def test_demo_mode_is_labelled_and_never_claimed_as_live(services, report):
    assert services.settings.is_demo is True
    assert report["data_classification"]["live_data"] == []
    assert report["data_classification"]["demo_data"]
    assert "not real Amazon" in (report["notes"] or "") or "Demo" in report["source"] or "demo" in report["source"].lower()


def test_demo_data_is_deterministic():
    first = run(research_product(build_services(), PRODUCT))
    second = run(research_product(build_services(), PRODUCT))
    assert first["average_price"] == second["average_price"]
    assert first["overall_opportunity_score"] == second["overall_opportunity_score"]


def test_beginner_fit_checks_are_evaluated(report):
    checks = report["beginner_fit"]["criteria_checks"]
    assert set(checks) == {
        "price_in_199_699_band",
        "weight_under_500g",
        "margin_at_least_30_percent",
        "return_rate_under_8_percent",
    }
    assert all(isinstance(value, bool) for value in checks.values())


def test_risky_product_traits_are_penalised(services):
    risky = run(research_product(services, "glass christmas lamp shade with battery"))
    safe = run(research_product(services, "kitchen drawer organizer"))
    assert risky["beginner_score"] < safe["beginner_score"]
    assert risky["penalties"]


@pytest.mark.parametrize("bad_name", ["", "x", "a" * 250])
def test_invalid_product_name_returns_clean_error(services, bad_name):
    result = run(research_product(services, bad_name))
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_input"
    assert "Traceback" not in result["error"]["message"]


def test_invalid_marketplace_returns_clean_error(services):
    result = run(research_product(services, PRODUCT, "amazon.com"))
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_input"
    assert "amazon.in" in result["error"]["message"]


def test_score_weights_sum_to_one(report):
    assert round(sum(report["score_weights"].values()), 6) == 1.0


def test_scoring_bands():
    perfect = OpportunityScores(
        demand=100, profitability=100, competition=100,
        return_risk=100, sourcing_ease=100, beginner_friendliness=100,
    )
    assert score_opportunity(perfect).overall_opportunity_score == 100
    assert score_opportunity(perfect).recommendation == "Strong Opportunity"

    worst = OpportunityScores(
        demand=0, profitability=0, competition=0,
        return_risk=0, sourcing_ease=0, beginner_friendliness=0,
    )
    assert score_opportunity(worst).overall_opportunity_score == 0
    assert score_opportunity(worst).recommendation == "Avoid"


def test_penalty_points_reduce_the_score():
    scores = OpportunityScores(
        demand=80, profitability=80, competition=80,
        return_risk=80, sourcing_ease=80, beginner_friendliness=80,
    )
    assert score_opportunity(scores, ["fragile"], 10).overall_opportunity_score == 70
