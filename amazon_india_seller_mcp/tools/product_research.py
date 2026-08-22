"""MCP tool: ``research_product`` - the full product opportunity report."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from amazon_india_seller_mcp.database.models import ProductResearch, save_record
from amazon_india_seller_mcp.services import OpportunityScores, score_opportunity
from amazon_india_seller_mcp.tools import ServiceBundle, tool_handler

if TYPE_CHECKING:  # pragma: no cover - typing only
    from amazon_india_seller_mcp.tools import MCPServerType

logger = logging.getLogger(__name__)

# Assumptions used when the seller has not yet sourced the product.
ASSUMED_COST_RATIO = 0.32  # landed product cost as a share of the average selling price
ASSUMED_PACKAGING_COST = 12.0
ASSUMED_OTHER_COSTS = 8.0


class ProductResearchInput(BaseModel):
    """Input schema for ``research_product``."""

    product_name: str = Field(min_length=2, max_length=200, description="Product idea or keyword, e.g. 'silicone sink strainer'")
    marketplace: str = Field(default="amazon.in", description="Marketplace to research. Only 'amazon.in' is supported.")


@tool_handler
async def research_product(
    services: ServiceBundle, product_name: str, marketplace: str = "amazon.in"
) -> dict[str, Any]:
    """Research a product idea end to end and score the opportunity 0-100."""
    payload = ProductResearchInput(product_name=product_name, marketplace=marketplace)

    snapshot = await services.amazon.get_product_snapshot(payload.product_name, payload.marketplace)
    risk = services.amazon.assess_risk(payload.product_name, snapshot)
    demand = await services.trends.analyze_demand(payload.product_name, payload.marketplace, snapshot)
    competition = await services.amazon.analyze_competition(
        payload.product_name, payload.marketplace, max_competitors=20
    )

    estimated_product_cost = round(snapshot.price_avg * ASSUMED_COST_RATIO, 2)
    profit = services.pricing.calculate(
        selling_price=snapshot.price_avg,
        product_cost=estimated_product_cost,
        packaging_cost=ASSUMED_PACKAGING_COST,
        weight_grams=snapshot.weight_grams,
        fulfillment_method="FBA" if snapshot.weight_grams <= 1000 else "Easy Ship",
        category=snapshot.category,
        expected_return_rate=risk.return_rate_estimate,
        other_costs=ASSUMED_OTHER_COSTS,
    )

    scores = OpportunityScores(
        demand=demand["demand_score"],
        profitability=profit.profitability_score,
        competition=competition["competition_score"],
        return_risk=max(0.0, min(100.0, 100 - risk.return_rate_estimate * 650)),
        sourcing_ease=risk.sourcing_ease_score,
        beginner_friendliness=risk.beginner_score,
    )
    opportunity = score_opportunity(scores, risk.penalties, risk.penalty_points)

    result = {
        "product_name": snapshot.product_name,
        "marketplace": snapshot.marketplace,
        "category": snapshot.category,
        "price_range": {"min": snapshot.price_min, "max": snapshot.price_max, "currency": "INR"},
        "average_price": snapshot.price_avg,
        "bsr": snapshot.bsr,
        "estimated_weight_grams": snapshot.weight_grams,
        "rating": snapshot.rating,
        "review_count": snapshot.review_count,
        "estimated_demand": demand["demand_level"],
        "demand_score": demand["demand_score"],
        "estimated_monthly_demand_units": demand["estimated_monthly_demand_units"],
        "trend_direction": demand["trend_direction"],
        "seasonality_risk": demand["seasonality_risk"],
        "competition_level": competition["competition_level"],
        "competition_score": competition["competition_score"],
        "return_risk": risk.return_risk,
        "estimated_return_rate": risk.return_rate_estimate,
        "gated_category_risk": risk.gated_category_risk,
        "brand_approval_risk": risk.brand_approval_risk,
        "beginner_score": risk.beginner_score,
        "risk_traits": risk.traits,
        "overall_opportunity_score": opportunity.overall_opportunity_score,
        "scores": opportunity.scores.model_dump(),
        "score_weights": opportunity.weights,
        "penalties": opportunity.penalties,
        "recommendation": opportunity.recommendation,
        "profitability_preview": {
            "assumptions": {
                "product_cost": estimated_product_cost,
                "product_cost_basis": f"{ASSUMED_COST_RATIO * 100:.0f}% of the average selling price (assumption, not a quotation)",
                "packaging_cost": ASSUMED_PACKAGING_COST,
                "other_costs": ASSUMED_OTHER_COSTS,
                "fulfillment_method": profit.fulfillment_method,
            },
            "estimated_profit_per_order": profit.estimated_profit,
            "profit_margin": profit.profit_margin,
            "roi": profit.roi,
            "break_even_price": profit.break_even_price,
            "data_type": "Estimated",
        },
        "beginner_fit": _beginner_fit(services, snapshot, profit.profit_margin, risk.return_rate_estimate),
        "data_provenance": {
            "marketplace_data": snapshot.envelope.as_dict(),
            "demand_data": {k: demand[k] for k in ("source", "data_type", "confidence", "last_updated")},
            "competition_data": {k: competition[k] for k in ("source", "data_type", "confidence", "last_updated")},
            "fee_data": services.pricing.envelope().as_dict(),
        },
        "data_classification": {
            "live_data": [],
            "estimated_data": [
                "demand", "monthly demand units", "profitability preview", "return rate", "weight",
            ],
            "historical_data": [],
            "demo_data": (
                ["price band", "BSR", "ratings", "review counts", "competitor listings"]
                if services.settings.is_demo
                else []
            ),
        },
        "next_steps": [
            "Run calculate_profitability with a real supplier quotation instead of the assumed cost.",
            "Run analyze_reviews on the top competitor to find the complaint you can fix.",
            "Run search_suppliers to plan sourcing, then validate the quotation with samples.",
        ],
        **snapshot.envelope.as_dict(),
    }

    await asyncio.to_thread(
        save_record,
        ProductResearch,
        product_name=snapshot.product_name,
        marketplace=snapshot.marketplace,
        research_data=result,
        data_source=snapshot.envelope.source,
        data_type=snapshot.envelope.data_type.value,
        confidence=snapshot.envelope.confidence.value,
        notes=snapshot.envelope.notes,
        opportunity_score=opportunity.overall_opportunity_score,
        recommendation=opportunity.recommendation,
        category=snapshot.category,
    )
    return result


def _beginner_fit(
    services: ServiceBundle, snapshot: Any, margin: float, return_rate: float
) -> dict[str, Any]:
    """Check the product against the beginner seller criteria."""
    criteria = services.settings.beginner_criteria
    checks = {
        "price_in_199_699_band": criteria.min_selling_price_inr <= snapshot.price_avg <= criteria.max_selling_price_inr,
        "weight_under_500g": snapshot.weight_grams <= criteria.max_weight_grams,
        "margin_at_least_30_percent": margin >= criteria.min_profit_margin,
        "return_rate_under_8_percent": return_rate <= criteria.max_return_rate,
    }
    passed = sum(1 for value in checks.values() if value)
    return {
        "criteria_checks": checks,
        "criteria_passed": f"{passed}/{len(checks)}",
        "verdict": (
            "Good beginner fit" if passed == len(checks)
            else "Workable with adjustments" if passed >= len(checks) - 1
            else "Poor beginner fit"
        ),
    }


def register(mcp: "MCPServerType", services: ServiceBundle) -> None:
    """Register ``research_product`` with the MCP server."""

    @mcp.tool(
        name="research_product",
        description=(
            "Research an Amazon India product idea: category, price band, BSR, weight, rating, demand, "
            "competition, return/gating/brand risk, beginner fit and an overall 0-100 opportunity score. "
            "Every figure is labelled Live, Estimated, Historical or Demo."
        ),
    )
    async def _research_product(product_name: str, marketplace: str = "amazon.in") -> dict[str, Any]:
        return await research_product(services, product_name, marketplace)
