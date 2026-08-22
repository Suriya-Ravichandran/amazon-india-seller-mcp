"""MCP tool: ``find_product_opportunities`` - screen and rank many ideas at once."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from services import InvalidInputError, OpportunityScores, score_opportunity
from tools import ServiceBundle, tool_handler

if TYPE_CHECKING:  # pragma: no cover
    from tools import MCPServerType

logger = logging.getLogger(__name__)

# Product ideas that fit the beginner brief, used when the caller has none.
STARTER_IDEAS: list[str] = [
    "silicone sink strainer",
    "cable organizer",
    "kitchen drawer organizer",
    "soap dispenser",
    "mobile stand",
    "spice container set",
    "laundry bag",
    "desk cable clip",
    "pet hair remover",
    "vegetable chopper",
    "shoe rack organizer",
    "fridge storage box",
]

ASSUMED_COST_RATIO = 0.32


class OpportunityFinderInput(BaseModel):
    """Input schema for ``find_product_opportunities``."""

    product_ideas: list[str] = Field(default_factory=list, max_length=15)
    marketplace: str = Field(default="amazon.in")
    max_investment: float = Field(default=20_000, gt=0)
    min_margin: float = Field(default=0.30, ge=0, le=0.9)
    max_weight_grams: float = Field(default=500, gt=0)


@tool_handler
async def find_product_opportunities(
    services: ServiceBundle,
    product_ideas: list[str] | None = None,
    marketplace: str = "amazon.in",
    max_investment: float = 20_000,
    min_margin: float = 0.30,
    max_weight_grams: float = 500,
) -> dict[str, Any]:
    """Screen a list of product ideas against the beginner criteria and rank them.

    This is the shortlisting step: run it first, then run ``research_product``
    on the two or three that survive.
    """
    payload = OpportunityFinderInput(
        product_ideas=product_ideas or [],
        marketplace=marketplace,
        max_investment=max_investment,
        min_margin=min_margin,
        max_weight_grams=max_weight_grams,
    )
    ideas = [idea.strip() for idea in payload.product_ideas if idea and idea.strip()] or STARTER_IDEAS
    if len(ideas) > 15:
        raise InvalidInputError("Screen at most 15 ideas at a time.")

    marketplace = services.amazon.validate_marketplace(payload.marketplace)
    results = await asyncio.gather(
        *(_screen_one(services, idea, marketplace, payload) for idea in ideas), return_exceptions=True
    )

    screened: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for idea, outcome in zip(ideas, results):
        if isinstance(outcome, BaseException):
            failures.append({"product": idea, "reason": str(outcome)[:160]})
        else:
            screened.append(outcome)

    screened.sort(key=lambda row: -row["overall_opportunity_score"])
    passing = [row for row in screened if row["passes_all_filters"]]

    return {
        "marketplace": marketplace,
        "ideas_screened": len(screened),
        "ideas_passing_filters": len(passing),
        "filters_applied": {
            "max_investment_inr": payload.max_investment,
            "min_profit_margin": payload.min_margin,
            "max_weight_grams": payload.max_weight_grams,
            "price_band_inr": "199-699",
        },
        "ranked_opportunities": screened,
        "shortlist": [row["product_name"] for row in passing[:3]],
        "recommendation": _recommendation(passing, screened),
        "failures": failures,
        "used_default_ideas": not payload.product_ideas,
        "next_steps": [
            "Run research_product on each shortlisted idea for the full report.",
            "Run analyze_competitors to check whether new sellers are winning on those keywords.",
            "Run search_suppliers, then replace the assumed product cost with a real quotation.",
        ],
        **services.amazon.provider.envelope("Screening uses the configured product data provider.").as_dict(),
    }


async def _screen_one(
    services: ServiceBundle, idea: str, marketplace: str, payload: OpportunityFinderInput
) -> dict[str, Any]:
    """Score one product idea against demand, profit, competition and beginner fit."""
    snapshot = await services.amazon.get_product_snapshot(idea, marketplace)
    risk = services.amazon.assess_risk(idea, snapshot)
    demand = await services.trends.analyze_demand(idea, marketplace, snapshot)

    profit = services.pricing.calculate(
        selling_price=snapshot.price_avg,
        product_cost=round(snapshot.price_avg * ASSUMED_COST_RATIO, 2),
        packaging_cost=12.0,
        weight_grams=snapshot.weight_grams,
        fulfillment_method="FBA",
        category=snapshot.category,
        expected_return_rate=risk.return_rate_estimate,
        other_costs=8.0,
    )
    competition = await services.amazon.analyze_competition(idea, marketplace, max_competitors=15)

    scores = OpportunityScores(
        demand=demand["demand_score"],
        profitability=profit.profitability_score,
        competition=competition["competition_score"],
        return_risk=max(0.0, min(100.0, 100 - risk.return_rate_estimate * 650)),
        sourcing_ease=risk.sourcing_ease_score,
        beginner_friendliness=risk.beginner_score,
    )
    opportunity = score_opportunity(scores, risk.penalties, risk.penalty_points)

    unit_cash = profit.product_cost + profit.packaging_cost + profit.other_costs
    units_affordable = int(payload.max_investment // unit_cash) if unit_cash else 0
    filters = {
        "price_in_band": 199 <= snapshot.price_avg <= 699,
        "weight_ok": snapshot.weight_grams <= payload.max_weight_grams,
        "margin_ok": profit.profit_margin >= payload.min_margin,
        "non_seasonal": demand["seasonality_risk"] in {"Low", "Very Low"},
        "affordable_first_order": units_affordable >= 25,
    }

    return {
        "product_name": idea,
        "category": snapshot.category,
        "average_price": snapshot.price_avg,
        "estimated_weight_grams": snapshot.weight_grams,
        "demand_level": demand["demand_level"],
        "demand_score": demand["demand_score"],
        "competition_level": competition["competition_level"],
        "profit_margin_percent": round(profit.profit_margin * 100, 1),
        "estimated_profit_per_order": profit.estimated_profit,
        "price_for_target_margin": profit.recommended_selling_price,
        "pricing_note": (
            f"At the market average ₹{snapshot.price_avg:.0f} the margin is "
            f"{profit.profit_margin * 100:.1f}%. Pricing at ₹{profit.recommended_selling_price:.0f} "
            f"would reach the {payload.min_margin * 100:.0f}% target, if the market bears it."
        ),
        "seasonality_risk": demand["seasonality_risk"],
        "overall_opportunity_score": opportunity.overall_opportunity_score,
        "recommendation": opportunity.recommendation,
        "scores": opportunity.scores.model_dump(),
        "first_order_units_affordable": units_affordable,
        "filters": filters,
        "passes_all_filters": all(filters.values()),
        "failed_filters": [name for name, passed in filters.items() if not passed],
        "risk_traits": risk.traits,
    }


def _recommendation(passing: list[dict[str, Any]], screened: list[dict[str, Any]]) -> str:
    if not screened:
        return "Nothing could be screened - check the product data provider."
    if not passing:
        best = screened[0]
        return (
            f"No idea cleared every filter. The closest is '{best['product_name']}' at "
            f"{best['overall_opportunity_score']}/100, failing: {', '.join(best['failed_filters'])}. "
            "Either relax a filter or bring new ideas."
        )
    top = passing[0]
    return (
        f"Start with '{top['product_name']}' - {top['overall_opportunity_score']}/100 "
        f"({top['recommendation']}), {top['profit_margin_percent']}% margin at ₹{top['average_price']:.0f}. "
        f"{len(passing)} of {len(screened)} ideas cleared every filter."
    )


def register(mcp: "MCPServerType", services: ServiceBundle) -> None:
    """Register ``find_product_opportunities`` with the MCP server."""

    @mcp.tool(
        name="find_product_opportunities",
        description=(
            "Screen up to 15 product ideas at once against the beginner Amazon India criteria "
            "(₹199-₹699 price, under 500 g, 30%+ margin, non-seasonal, affordable first order) and rank "
            "them by opportunity score. Omit product_ideas to screen a built-in starter list. Use this "
            "to shortlist, then run research_product on the winners."
        ),
    )
    async def _find_product_opportunities(
        product_ideas: list[str] | None = None,
        marketplace: str = "amazon.in",
        max_investment: float = 20_000,
        min_margin: float = 0.30,
        max_weight_grams: float = 500,
    ) -> dict[str, Any]:
        return await find_product_opportunities(
            services, product_ideas, marketplace, max_investment, min_margin, max_weight_grams
        )
