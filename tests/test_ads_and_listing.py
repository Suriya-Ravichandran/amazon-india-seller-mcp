"""Tests for Amazon Ads bid maths and full listing-detail parsing."""

from __future__ import annotations

import pytest

from amazon_india_seller_mcp.config.settings import Settings
from amazon_india_seller_mcp.services.ads_service import MATCH_TYPE_MULTIPLIERS, AdsService
from amazon_india_seller_mcp.services.scraper_service import _badge_text, _specifications, _parse
from tests import build_services, run
from amazon_india_seller_mcp.tools.listing_scraper import _score_listing
from amazon_india_seller_mcp.tools.ppc_bidding import calculate_ppc_bids, plan_ppc_campaign
from amazon_india_seller_mcp.tools.ppc_keywords import suggest_ppc_keywords


@pytest.fixture(scope="module")
def services():
    return build_services()


@pytest.fixture(scope="module")
def ads():
    return AdsService(Settings())


# -- bid maths -------------------------------------------------------------- #
def test_break_even_acos_equals_the_profit_margin(ads):
    """The core identity of PPC: spend your whole margin and profit is zero."""
    math = ads.bid_math(selling_price=400, profit_per_unit=100, conversion_rate=0.10)
    assert math.break_even_acos == pytest.approx(0.25, abs=0.001)
    assert math.profit_margin == pytest.approx(0.25, abs=0.001)


def test_cpc_follows_price_times_conversion_times_acos(ads):
    math = ads.bid_math(selling_price=400, profit_per_unit=100, conversion_rate=0.10, target_acos=0.20)
    assert math.target_cpc == pytest.approx(400 * 0.10 * 0.20, abs=0.01)
    assert math.break_even_cpc == pytest.approx(400 * 0.10 * 0.25, abs=0.01)


def test_break_even_cpc_is_never_below_target_cpc(ads):
    math = ads.bid_math(selling_price=399, profit_per_unit=140, conversion_rate=0.08)
    assert math.break_even_cpc >= math.target_cpc


def test_clicks_per_order_is_the_inverse_of_conversion_rate(ads):
    assert ads.bid_math(selling_price=400, profit_per_unit=100, conversion_rate=0.10).clicks_per_order == 10
    assert ads.bid_math(selling_price=400, profit_per_unit=100, conversion_rate=0.05).clicks_per_order == 20


def test_higher_conversion_rate_allows_a_higher_bid(ads):
    low = ads.bid_math(selling_price=400, profit_per_unit=100, conversion_rate=0.05)
    high = ads.bid_math(selling_price=400, profit_per_unit=100, conversion_rate=0.15)
    assert high.target_cpc > low.target_cpc


def test_advertising_a_loss_making_product_is_refused(ads):
    with pytest.raises(Exception) as excinfo:
        ads.bid_math(selling_price=199, profit_per_unit=-10)
    assert "positive" in str(excinfo.value).lower()


@pytest.mark.parametrize("bad_rate", [0, 1.5, -0.2])
def test_invalid_conversion_rate_rejected(ads, bad_rate):
    with pytest.raises(Exception):
        ads.bid_math(selling_price=400, profit_per_unit=100, conversion_rate=bad_rate)


def test_match_type_bid_ladder_is_ordered(ads):
    assert MATCH_TYPE_MULTIPLIERS["exact"] > MATCH_TYPE_MULTIPLIERS["phrase"]
    assert MATCH_TYPE_MULTIPLIERS["phrase"] > MATCH_TYPE_MULTIPLIERS["broad"]
    assert MATCH_TYPE_MULTIPLIERS["broad"] > MATCH_TYPE_MULTIPLIERS["auto"]


def test_no_suggested_bid_exceeds_break_even(ads):
    math = ads.bid_math(selling_price=399, profit_per_unit=140, conversion_rate=0.10)
    keywords = [
        {"keyword": "silicone sink strainer", "group": "Primary", "competition_index": 0.95},
        {"keyword": "sink strainer for kitchen sink", "group": "Long-Tail", "competition_index": 0.2},
    ]
    for row in ads.build_keyword_bids(keywords, math):
        assert row.suggested_bid <= math.break_even_cpc * 1.11
        assert row.bid_range["low"] <= row.suggested_bid <= row.bid_range["high"]


def test_keyword_placement_matches_intent(ads):
    math = ads.bid_math(selling_price=399, profit_per_unit=140, conversion_rate=0.10)
    rows = {
        row.keyword: row
        for row in ads.build_keyword_bids(
            [
                {"keyword": "sink strainer", "group": "Primary", "competition_index": 0.6},
                {"keyword": "silicone sink strainer for indian kitchen", "group": "Long-Tail", "competition_index": 0.2},
            ],
            math,
        )
    }
    assert rows["sink strainer"].match_type == "exact"
    assert rows["silicone sink strainer for indian kitchen"].match_type == "phrase"


# -- tools ------------------------------------------------------------------ #
def test_calculate_ppc_bids_end_to_end(services):
    report = run(calculate_ppc_bids(services, selling_price=399, product_cost=120))
    assert report["ok"] is True
    assert report["acos"]["break_even_acos_percent"] > 0
    assert len(report["bid_ladder"]) == 4
    assert report["data_type"] == "Estimated"


def test_current_cpc_above_break_even_is_flagged(services):
    report = run(calculate_ppc_bids(services, selling_price=399, product_cost=120, current_cpc=200))
    assert "Losing money" in report["current_bid_check"]["verdict"]


def test_current_cpc_below_break_even_is_approved(services):
    report = run(calculate_ppc_bids(services, selling_price=399, product_cost=120, current_cpc=2))
    assert "Profitable" in report["current_bid_check"]["verdict"]


def test_bids_require_profit_or_cost(services):
    report = run(calculate_ppc_bids(services, selling_price=399))
    assert report["ok"] is False
    assert report["error"]["code"] == "invalid_input"


def test_keyword_suggestions_carry_match_type_and_bid(services):
    report = run(suggest_ppc_keywords(services, "silicone sink strainer", selling_price=399, product_cost=120))
    assert report["ok"] is True
    assert report["keyword_count"] > 0
    for row in report["keywords"]:
        assert row["match_type"] in {"exact", "phrase", "broad", "auto"}
        assert row["suggested_bid"] > 0
        assert row["campaign"]
    assert report["negative_keywords"]["add_immediately"]


def test_campaign_plan_budget_splits_add_up(services):
    report = run(
        plan_ppc_campaign(services, "Sink Strainer", selling_price=399, product_cost=120, monthly_ad_budget=6_000)
    )
    assert report["ok"] is True
    daily_total = sum(campaign["daily_budget"] for campaign in report["campaigns"])
    assert daily_total == pytest.approx(6_000 / 30, abs=0.5)
    assert len(report["campaigns"]) == 3


def test_thin_margin_product_is_warned_about(services):
    report = run(
        plan_ppc_campaign(services, "Thin Margin", selling_price=249, product_cost=150, monthly_ad_budget=3_000)
    )
    if report["ok"]:
        assert report["warnings"], "a thin-margin product must produce a warning"
    else:
        assert report["error"]["code"] == "invalid_input"


def test_ads_output_is_never_labelled_live(services):
    report = run(calculate_ppc_bids(services, selling_price=399, product_cost=120))
    assert report["data_type"] != "Live"
    assert "assumption" in (report["notes"] or "").lower()


# -- listing detail parsing ------------------------------------------------- #
SPEC_TABLE_HTML = """
<table id="productDetails_techSpec_section_1">
  <tr><th>Material</th><td>Silicone</td></tr>
  <tr><th>Item Weight</th><td>90 g</td></tr>
  <tr><th>Colour</th><td>Grey</td></tr>
</table>
"""

DETAIL_BULLETS_HTML = """
<div id="detailBullets_feature_div"><ul>
  <li><span class="a-list-item"><span class="a-text-bold">Material ‎ : ‏</span><span>Food Grade Silicone</span></span></li>
  <li><span class="a-list-item"><span class="a-text-bold">Item Weight ‎ : ‏</span><span>90 Grams</span></span></li>
</ul></div>
"""


def test_specifications_parse_from_a_real_table():
    specs = _specifications(_parse(SPEC_TABLE_HTML), "#productDetails_techSpec_section_1 tr")
    assert specs["Material"] == "Silicone"
    assert specs["Item Weight"] == "90 g"
    assert len(specs) == 3


def test_specifications_parse_from_detail_bullets():
    """The container span must be discarded, or every row parses as key=key."""
    specs = _specifications(_parse(DETAIL_BULLETS_HTML), "#detailBullets_feature_div li")
    assert specs["Material"] == "Food Grade Silicone"
    assert specs["Item Weight"] == "90 Grams"


def test_badge_text_ignores_json_config_blobs():
    assert _badge_text(_parse('<div>{"acAsin":"B0D41Y1BHN"}</div>').css_first("div")) is None
    assert _badge_text(_parse("<div>Amazon's Choice</div>").css_first("div")) == "Amazon's Choice"


def test_listing_scorecard_rewards_a_complete_listing():
    complete = {
        "title_length": 150, "bullet_count": 5, "image_count": 7, "description_length": 1500,
        "has_aplus_content": True, "has_video": True, "specifications": {str(i): "x" for i in range(6)},
    }
    empty = {
        "title_length": 30, "bullet_count": 1, "image_count": 1, "description_length": 0,
        "has_aplus_content": False, "has_video": False, "specifications": {},
    }
    _, strong_score = _score_listing(complete)
    rows, weak_score = _score_listing(empty)
    assert strong_score >= 95
    assert weak_score < 40
    assert strong_score > weak_score
    assert any(row["verdict"] == "Missing" for row in rows)


def test_listing_scorecard_points_never_exceed_the_maximum():
    rows, score = _score_listing({"title_length": 150, "bullet_count": 20, "image_count": 40})
    assert score <= 100
    for row in rows:
        assert row["points_earned"] <= row["points_available"]
