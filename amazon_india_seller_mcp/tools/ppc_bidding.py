"""MCP tools: ``calculate_ppc_bids`` and ``plan_ppc_campaign``.

The bid maths and campaign structure live in :mod:`services.ads_service`; these
tools validate input, add the seller-facing interpretation, and return it.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from amazon_india_seller_mcp.services import InvalidInputError
from amazon_india_seller_mcp.tools import ServiceBundle, tool_handler

if TYPE_CHECKING:  # pragma: no cover
    from amazon_india_seller_mcp.tools import MCPServerType

logger = logging.getLogger(__name__)


class BidInput(BaseModel):
    """Input schema for ``calculate_ppc_bids``."""

    selling_price: float = Field(gt=0, le=500_000)
    profit_per_unit: float | None = Field(default=None)
    product_cost: float | None = Field(default=None, ge=0)
    category: str = Field(default="Home & Kitchen")
    conversion_rate: float | None = Field(default=None, gt=0, le=1)
    target_acos: float | None = Field(default=None, gt=0, le=2)
    current_cpc: float | None = Field(default=None, gt=0)


class CampaignInput(BaseModel):
    """Input schema for ``plan_ppc_campaign``."""

    product_name: str = Field(min_length=2, max_length=200)
    selling_price: float = Field(gt=0, le=500_000)
    product_cost: float = Field(ge=0)
    monthly_ad_budget: float = Field(default=6_000, gt=0)
    category: str = Field(default="Home & Kitchen")
    target_acos: float | None = Field(default=None, gt=0, le=2)
    conversion_rate: float | None = Field(default=None, gt=0, le=1)
    launch_phase: bool = Field(default=True, description="Launch phase accepts a higher ACOS to buy velocity")


def _resolve_profit(services: ServiceBundle, price: float, profit: float | None, cost: float | None, category: str):
    """Get profit per unit from the caller, or compute it from the product cost."""
    if profit is not None:
        return profit, None
    if cost is None:
        raise InvalidInputError(
            "Provide either profit_per_unit or product_cost.",
            remediation="Pass product_cost=120, or run calculate_profitability first.",
        )
    breakdown = services.pricing.calculate(selling_price=price, product_cost=cost, category=category)
    return breakdown.estimated_profit, {
        "profit_per_unit": breakdown.estimated_profit,
        "profit_margin_percent": round(breakdown.profit_margin * 100, 2),
        "total_cost_per_unit": breakdown.total_cost,
        "amazon_fees": breakdown.amazon_fees,
    }


@tool_handler
async def calculate_ppc_bids(
    services: ServiceBundle,
    selling_price: float,
    profit_per_unit: float | None = None,
    product_cost: float | None = None,
    category: str = "Home & Kitchen",
    conversion_rate: float | None = None,
    target_acos: float | None = None,
    current_cpc: float | None = None,
) -> dict[str, Any]:
    """Work out break-even ACOS, break-even CPC and the bid you can actually afford."""
    payload = BidInput(
        selling_price=selling_price,
        profit_per_unit=profit_per_unit,
        product_cost=product_cost,
        category=category,
        conversion_rate=conversion_rate,
        target_acos=target_acos,
        current_cpc=current_cpc,
    )
    profit, unit_economics = _resolve_profit(
        services, payload.selling_price, payload.profit_per_unit, payload.product_cost, payload.category
    )
    math = services.ads.bid_math(
        selling_price=payload.selling_price,
        profit_per_unit=profit,
        category=payload.category,
        conversion_rate=payload.conversion_rate,
        target_acos=payload.target_acos,
    )
    floor, ceiling = services.ads.cpc_band_for(payload.category)

    result: dict[str, Any] = {
        "currency": "INR",
        "category": payload.category,
        "unit_economics": unit_economics,
        "acos": {
            "break_even_acos_percent": round(math.break_even_acos * 100, 2),
            "target_acos_percent": round(math.target_acos * 100, 2),
            "roas_at_target": math.roas_at_target,
            "explanation": (
                f"Break-even ACOS equals your {math.profit_margin * 100:.1f}% margin: spend more than "
                f"{math.break_even_acos * 100:.1f}% of revenue on ads and each order loses money."
            ),
        },
        "cpc": {
            "break_even_cpc": math.break_even_cpc,
            "target_cpc": math.target_cpc,
            "category_typical_range": {"low": floor, "high": ceiling},
            "explanation": (
                f"CPC = price x conversion rate x ACOS. At Rs.{math.selling_price:.0f}, a "
                f"{math.conversion_rate * 100:.0f}% conversion rate and a {math.target_acos * 100:.0f}% "
                f"target ACOS, you can pay Rs.{math.target_cpc:.2f} per click."
            ),
        },
        "bid_ladder": [
            {
                "match_type": match,
                "suggested_bid": round(math.target_cpc * multiplier, 2),
                "maximum_bid": round(math.break_even_cpc * multiplier, 2),
                "note": note,
            }
            for match, multiplier, note in (
                ("exact", 1.00, "Highest bid: the most qualified traffic you can buy."),
                ("phrase", 0.85, "Mid-tail discovery with some control."),
                ("broad", 0.70, "Widest reach; needs negatives to stay efficient."),
                ("auto", 0.65, "Discovery campaign; harvest its search terms weekly."),
            )
        ],
        "per_order_economics": {
            "clicks_needed_per_order": math.clicks_per_order,
            "ad_cost_per_order": math.ad_cost_per_order,
            "profit_before_ads": math.profit_per_unit,
            "profit_after_ads": math.profit_after_ads,
            "verdict": (
                f"Each order should cost about Rs.{math.ad_cost_per_order:.2f} in ads, leaving "
                f"Rs.{math.profit_after_ads:.2f} profit."
                if math.profit_after_ads > 0
                else "At this target ACOS ads consume the entire margin. Lower the target or raise the price."
            ),
        },
        "assumptions": [
            f"Conversion rate {math.conversion_rate * 100:.1f}% ({payload.category} benchmark)"
            if payload.conversion_rate is None
            else f"Conversion rate {math.conversion_rate * 100:.1f}% (supplied by you)",
            "Excludes organic sales; ACOS here is ad spend against ad sales only.",
            "Assumes the listing converts; poor images or reviews will push the real rate lower.",
        ],
        **services.ads.envelope().as_dict(),
    }

    if payload.current_cpc:
        headroom = math.break_even_cpc - payload.current_cpc
        result["current_bid_check"] = {
            "your_cpc": payload.current_cpc,
            "break_even_cpc": math.break_even_cpc,
            "headroom": round(headroom, 2),
            "implied_acos_percent": round(
                payload.current_cpc / (math.selling_price * math.conversion_rate) * 100, 2
            ),
            "verdict": (
                f"Profitable: Rs.{headroom:.2f} below break-even."
                if headroom > 0
                else f"Losing money: Rs.{abs(headroom):.2f} above the break-even CPC. Cut the bid."
            ),
        }
    return result


@tool_handler
async def plan_ppc_campaign(
    services: ServiceBundle,
    product_name: str,
    selling_price: float,
    product_cost: float,
    monthly_ad_budget: float = 6_000,
    category: str = "Home & Kitchen",
    target_acos: float | None = None,
    conversion_rate: float | None = None,
    launch_phase: bool = True,
) -> dict[str, Any]:
    """Build a full Sponsored Products campaign plan: structure, bids and budget."""
    payload = CampaignInput(
        product_name=product_name,
        selling_price=selling_price,
        product_cost=product_cost,
        monthly_ad_budget=monthly_ad_budget,
        category=category,
        target_acos=target_acos,
        conversion_rate=conversion_rate,
        launch_phase=launch_phase,
    )
    profit, unit_economics = _resolve_profit(
        services, payload.selling_price, None, payload.product_cost, payload.category
    )

    # During launch, deliberately accept an ACOS above break-even to buy velocity.
    resolved_acos = payload.target_acos
    if resolved_acos is None and payload.launch_phase:
        resolved_acos = min(1.2, (profit / payload.selling_price) * 1.15)

    math = services.ads.bid_math(
        selling_price=payload.selling_price,
        profit_per_unit=profit,
        category=payload.category,
        conversion_rate=payload.conversion_rate,
        target_acos=resolved_acos,
    )
    research = await services.trends.research_keywords(payload.product_name)
    rows = services.ads.build_keyword_bids(research["keyword_table"], math, payload.category)

    daily_budget = round(payload.monthly_ad_budget / 30, 2)
    structure = services.ads.campaign_structure(payload.product_name, math, daily_budget, rows)
    clicks = payload.monthly_ad_budget / math.target_cpc if math.target_cpc else 0
    orders = clicks * math.conversion_rate

    return {
        "product_name": payload.product_name,
        "currency": "INR",
        "unit_economics": unit_economics,
        "phase": "Launch" if payload.launch_phase else "Profitability",
        "acos": {
            "break_even_percent": round(math.break_even_acos * 100, 2),
            "target_percent": round(math.target_acos * 100, 2),
            "strategy": (
                "Launch phase: running above break-even on purpose to buy rank and reviews. "
                "Tighten to below break-even once you hold page-one organic placement."
                if payload.launch_phase and math.target_acos >= math.break_even_acos
                else "Profitability phase: every campaign should pay for itself."
            ),
        },
        "budget": {
            "monthly": payload.monthly_ad_budget,
            "daily": daily_budget,
            "estimated_monthly_clicks": int(clicks),
            "estimated_monthly_orders": int(orders),
            "estimated_ad_sales": round(orders * payload.selling_price, 2),
            "estimated_profit_from_ads": round(orders * math.profit_after_ads, 2),
        },
        "campaigns": structure,
        "top_keywords": [row.model_dump() for row in rows[:15]],
        "negative_keywords": services.ads.negative_keywords(payload.product_name, [row.keyword for row in rows]),
        "weekly_routine": [
            "Download the search-term report and negate anything with 15+ clicks and no orders.",
            "Move converting search terms from Auto into the Exact campaign as their own keyword.",
            f"Raise bids 10-15% on keywords converting under {math.target_acos * 100:.0f}% ACOS.",
            f"Cut bids or pause anything above the Rs.{math.break_even_cpc:.2f} break-even CPC that is not converting.",
            "Check the impression share on your core exact keywords; if it is low, the bid is too low.",
        ],
        "warnings": _warnings(math, payload),
        **services.ads.envelope().as_dict(),
    }


def _warnings(math: Any, payload: CampaignInput) -> list[str]:
    warnings: list[str] = []
    if math.profit_after_ads <= 0:
        warnings.append(
            "At this target ACOS, ads eat the entire unit profit. Only acceptable as a short, "
            "deliberate launch push - never as a steady state."
        )
    if math.break_even_cpc < 3:
        warnings.append(
            f"Break-even CPC is only Rs.{math.break_even_cpc:.2f}, below what most Amazon India clicks cost. "
            "This product is probably too thin-margin to advertise profitably."
        )
    if payload.monthly_ad_budget < math.ad_cost_per_order * 30:
        warnings.append(
            f"A budget of Rs.{payload.monthly_ad_budget:.0f}/month buys roughly "
            f"{int(payload.monthly_ad_budget / math.ad_cost_per_order)} orders. That may be too little "
            "data to optimise on."
        )
    if math.profit_margin < 0.25:
        warnings.append(
            f"A {math.profit_margin * 100:.1f}% margin leaves very little room for ads. Fix the unit "
            "economics before scaling spend."
        )
    return warnings


def register(mcp: "MCPServerType", services: ServiceBundle) -> None:
    """Register the PPC bidding tools with the MCP server."""

    @mcp.tool(
        name="calculate_ppc_bids",
        description=(
            "Calculate Amazon Ads bids from unit economics: break-even ACOS (equal to your margin), "
            "target ACOS, break-even and target CPC, a bid ladder for exact / phrase / broad / auto "
            "match types, clicks needed per order, ad cost per order and profit after ads. Pass "
            "current_cpc to check whether a bid you are already running is profitable."
        ),
    )
    async def _calculate_ppc_bids(
        selling_price: float,
        profit_per_unit: float | None = None,
        product_cost: float | None = None,
        category: str = "Home & Kitchen",
        conversion_rate: float | None = None,
        target_acos: float | None = None,
        current_cpc: float | None = None,
    ) -> dict[str, Any]:
        return await calculate_ppc_bids(
            services, selling_price, profit_per_unit, product_cost, category,
            conversion_rate, target_acos, current_cpc,
        )

    @mcp.tool(
        name="plan_ppc_campaign",
        description=(
            "Build a complete Sponsored Products plan for a product on Amazon India: a three-campaign "
            "structure (Auto discovery, Manual Exact core, Phrase/Broad expansion) with the budget split "
            "across them, default bids per campaign, keyword assignments, negative keywords, projected "
            "clicks / orders / ad sales, a weekly optimisation routine, and warnings when the margin "
            "cannot support advertising."
        ),
    )
    async def _plan_ppc_campaign(
        product_name: str,
        selling_price: float,
        product_cost: float,
        monthly_ad_budget: float = 6_000,
        category: str = "Home & Kitchen",
        target_acos: float | None = None,
        conversion_rate: float | None = None,
        launch_phase: bool = True,
    ) -> dict[str, Any]:
        return await plan_ppc_campaign(
            services, product_name, selling_price, product_cost, monthly_ad_budget,
            category, target_acos, conversion_rate, launch_phase,
        )
