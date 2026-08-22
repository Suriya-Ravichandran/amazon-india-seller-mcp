"""Tests for the analyze_product_demand tool and the trends service."""

from __future__ import annotations

import pytest

from amazon_india_seller_mcp.services.trends_service import TrendsService
from tests import build_services, run
from amazon_india_seller_mcp.tools.demand_analysis import analyze_product_demand

LAUNCH_DECISIONS = {
    "Strong Opportunity", "Good Opportunity", "Moderate Opportunity", "High Risk", "Avoid",
}
DEMAND_LEVELS = {"Very Low", "Low", "Medium", "High", "Very High"}


@pytest.fixture(scope="module")
def services():
    return build_services()


@pytest.fixture(scope="module")
def report(services):
    return run(analyze_product_demand(services, "silicone sink strainer", "amazon.in"))


def test_demand_report_has_required_fields(report):
    assert report["ok"] is True
    for field in (
        "estimated_monthly_demand", "demand_level", "trend_direction", "seasonality",
        "demand_score", "confidence", "recommended_launch_decision",
    ):
        assert field in report


def test_demand_level_and_decision_use_the_defined_vocabularies(report):
    assert report["demand_level"] in DEMAND_LEVELS
    assert report["recommended_launch_decision"] in LAUNCH_DECISIONS
    assert report["trend_direction"] in {"Rising", "Stable", "Declining"}


def test_demand_score_within_range(report):
    assert 0 <= report["demand_score"] <= 100
    assert report["estimated_monthly_demand"] > 0


def test_demand_result_carries_provenance_and_caveat(report):
    for field in ("source", "data_type", "confidence", "last_updated"):
        assert report[field]
    assert report["data_type"] == "Demo"
    assert "not measured Amazon sales" in report["caveat"]


def test_seasonal_products_are_flagged(services):
    seasonal = run(analyze_product_demand(services, "woolen sweater"))
    assert seasonal["seasonality_risk"] in {"High", "Very High"}
    assert seasonal["seasonality"]["non_seasonal"] is False

    evergreen = run(analyze_product_demand(services, "kitchen drawer organizer"))
    assert evergreen["seasonality_risk"] == "Low"
    assert evergreen["seasonality"]["non_seasonal"] is True


def test_seasonality_lowers_the_launch_decision(services):
    seasonal = run(analyze_product_demand(services, "christmas gift box"))
    assert seasonal["recommended_launch_decision"] in LAUNCH_DECISIONS
    assert any("Seasonality" in reason for reason in seasonal["decision_reasons"])


def test_demo_demand_is_deterministic():
    first = run(analyze_product_demand(build_services(), "cable organizer"))
    second = run(analyze_product_demand(build_services(), "cable organizer"))
    assert first["demand_score"] == second["demand_score"]
    assert first["estimated_monthly_demand"] == second["estimated_monthly_demand"]


@pytest.mark.parametrize("bad_name", ["", "a"])
def test_invalid_input_returns_clean_error(services, bad_name):
    result = run(analyze_product_demand(services, bad_name))
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_input"


def test_invalid_marketplace_returns_clean_error(services):
    result = run(analyze_product_demand(services, "cable organizer", "amazon.co.uk"))
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_input"


def test_trends_service_can_run_without_a_marketplace_snapshot():
    demand = run(TrendsService().analyze_demand("mobile stand", "amazon.in", snapshot=None))
    assert demand["demand_level"] in DEMAND_LEVELS
    assert demand["signals_used"]
