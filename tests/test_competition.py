"""Tests for the analyze_competition tool."""

from __future__ import annotations

import pytest

from tests import build_services, run
from amazon_india_seller_mcp.tools.competition import analyze_competition

COMPETITION_LEVELS = {"Low", "Medium-Low", "Medium", "Medium-High", "High", "Very High"}


@pytest.fixture(scope="module")
def services():
    return build_services()


@pytest.fixture(scope="module")
def report(services):
    return run(analyze_competition(services, "cable organizer", "amazon.in", 20))


def test_competition_report_has_required_sections(report):
    assert report["ok"] is True
    for field in (
        "competition_level", "average_competitor_price", "average_rating",
        "average_review_count", "brand_dominance", "price_competition",
        "review_barrier", "listing_quality", "image_quality",
        "differentiation_opportunity", "weak_listings", "opportunities",
    ):
        assert field in report


def test_competition_level_uses_the_defined_vocabulary(report):
    assert report["competition_level"] in COMPETITION_LEVELS
    assert 0 <= report["competition_score"] <= 100


def test_opportunity_sections_are_populated(report):
    opportunities = report["opportunities"]
    for field in (
        "negative_review_opportunities", "bundle_opportunities",
        "product_improvement_opportunities", "keyword_opportunities",
    ):
        assert opportunities[field]


def test_competitor_count_is_respected(services):
    report = run(analyze_competition(services, "mobile stand", "amazon.in", 5))
    assert report["competitors_analysed"] == 5


def test_price_range_is_consistent(report):
    assert report["price_range"]["min"] <= report["average_competitor_price"] <= report["price_range"]["max"]


def test_weak_listings_carry_a_reason(report):
    for listing in report["weak_listings"]:
        assert listing["weakness"]
        assert 0 <= listing["listing_quality_score"] <= 100


def test_provenance_is_present_and_labelled_demo(report):
    for field in ("source", "data_type", "confidence", "last_updated"):
        assert report[field]
    assert report["data_type"] == "Demo"


def test_results_are_deterministic_in_demo_mode():
    first = run(analyze_competition(build_services(), "cable organizer", "amazon.in", 20))
    second = run(analyze_competition(build_services(), "cable organizer", "amazon.in", 20))
    assert first["average_competitor_price"] == second["average_competitor_price"]
    assert first["competition_level"] == second["competition_level"]


@pytest.mark.parametrize("max_competitors", [0, -1, 500])
def test_invalid_max_competitors_returns_clean_error(services, max_competitors):
    result = run(analyze_competition(services, "cable organizer", "amazon.in", max_competitors))
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_input"


def test_invalid_keyword_returns_clean_error(services):
    result = run(analyze_competition(services, "", "amazon.in", 10))
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_input"
    assert "Traceback" not in result["error"]["message"]
