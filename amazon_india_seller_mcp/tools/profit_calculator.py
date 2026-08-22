"""MCP tool: ``calculate_profitability`` - per-order Amazon India profit maths."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from amazon_india_seller_mcp.database.models import ProfitCalculation, save_record
from amazon_india_seller_mcp.tools import ServiceBundle, tool_handler

if TYPE_CHECKING:  # pragma: no cover
    from amazon_india_seller_mcp.tools import MCPServerType

logger = logging.getLogger(__name__)


class ProfitInput(BaseModel):
    """Input schema for ``calculate_profitability``."""

    selling_price: float = Field(gt=0, le=500_000)
    product_cost: float = Field(ge=0)
    packaging_cost: float = Field(default=0.0, ge=0)
    weight_grams: float = Field(default=250.0, gt=0, le=100_000)
    fulfillment_method: str = Field(default="FBA")
    category: str = Field(default="Home & Kitchen")
    expected_return_rate: float = Field(default=0.05, ge=0, le=0.9)
    other_costs: float = Field(default=0.0, ge=0)
    shipping_cost_override: float | None = Field(default=None, ge=0)


@tool_handler
async def calculate_profitability(
    services: ServiceBundle,
    selling_price: float,
    product_cost: float,
    packaging_cost: float = 0.0,
    weight_grams: float = 250.0,
    fulfillment_method: str = "FBA",
    category: str = "Home & Kitchen",
    expected_return_rate: float = 0.05,
    other_costs: float = 0.0,
    shipping_cost_override: float | None = None,
) -> dict[str, Any]:
    """Calculate profit, margin, ROI, break-even and a recommended price."""
    payload = ProfitInput(
        selling_price=selling_price,
        product_cost=product_cost,
        packaging_cost=packaging_cost,
        weight_grams=weight_grams,
        fulfillment_method=fulfillment_method,
        category=category,
        expected_return_rate=expected_return_rate,
        other_costs=other_costs,
        shipping_cost_override=shipping_cost_override,
    )

    breakdown = services.pricing.calculate(
        selling_price=payload.selling_price,
        product_cost=payload.product_cost,
        packaging_cost=payload.packaging_cost,
        weight_grams=payload.weight_grams,
        fulfillment_method=payload.fulfillment_method,
        category=payload.category,
        expected_return_rate=payload.expected_return_rate,
        other_costs=payload.other_costs,
        shipping_cost_override=payload.shipping_cost_override,
    )
    explanation = services.pricing.explain(breakdown)
    envelope = services.pricing.envelope()

    result = {
        "currency": "INR",
        "fulfillment_method": breakdown.fulfillment_method,
        "category": payload.category,
        "selling_price": breakdown.selling_price,
        "product_cost": breakdown.product_cost,
        "packaging_cost": breakdown.packaging_cost,
        "amazon_fees": breakdown.amazon_fees,
        "shipping_or_fulfillment_cost": breakdown.fulfillment_cost,
        "return_reserve": breakdown.return_reserve,
        "other_costs": breakdown.other_costs,
        "total_cost": breakdown.total_cost,
        "estimated_profit_per_order": breakdown.estimated_profit,
        "profit_margin": breakdown.profit_margin,
        "profit_margin_percent": round(breakdown.profit_margin * 100, 2),
        "roi": breakdown.roi,
        "roi_percent": round(breakdown.roi * 100, 2),
        "break_even_price": breakdown.break_even_price,
        "recommended_selling_price": breakdown.recommended_selling_price,
        "profitability_score": breakdown.profitability_score,
        "fee_breakdown": [line.model_dump() for line in breakdown.fee_lines],
        "return_reserve_basis": (
            f"{payload.expected_return_rate * 100:.1f}% of orders x (product + packaging + 1.5x fulfilment cost), "
            "covering goods lost and return shipping."
        ),
        "beginner_explanation": explanation,
        "investment_planning": _investment_planning(services, breakdown),
        "assumptions": [
            "Fees come from the configured schedule and exclude storage, removal and advertising costs.",
            "GST on your own product purchase (input credit) is not modelled.",
            "Advertising cost per order is not included - budget from the profit shown.",
        ],
        **envelope.as_dict(),
    }

    await asyncio.to_thread(
        save_record,
        ProfitCalculation,
        product_name=payload.category,
        marketplace="amazon.in",
        research_data=result,
        data_source=envelope.source,
        data_type=envelope.data_type.value,
        confidence=envelope.confidence.value,
        notes=envelope.notes,
        selling_price=breakdown.selling_price,
        total_cost=breakdown.total_cost,
        estimated_profit=breakdown.estimated_profit,
        profit_margin=breakdown.profit_margin,
        roi=breakdown.roi,
        fulfillment_method=breakdown.fulfillment_method,
    )
    return result


def _investment_planning(services: ServiceBundle, breakdown: Any) -> dict[str, Any]:
    """Translate per-unit economics into first-order investment planning."""
    criteria = services.settings.beginner_criteria
    unit_cash = breakdown.product_cost + breakdown.packaging_cost + breakdown.other_costs
    if unit_cash <= 0:
        return {"note": "Provide a product cost to size a first order."}
    min_units = int(criteria.min_investment_inr // unit_cash)
    max_units = int(criteria.max_investment_inr // unit_cash)
    return {
        "cash_per_unit": round(unit_cash, 2),
        "units_for_5000_investment": min_units,
        "units_for_20000_investment": max_units,
        "profit_if_all_units_sell_at_20000_investment": round(max_units * breakdown.estimated_profit, 2),
        "caveat": "Sell-through is never guaranteed. Treat this as planning maths, not a projection of sales.",
    }


def register(mcp: "MCPServerType", services: ServiceBundle) -> None:
    """Register ``calculate_profitability`` with the MCP server."""

    @mcp.tool(
        name="calculate_profitability",
        description=(
            "Calculate Amazon India per-order profitability: referral fee, closing fee, FBA / Easy Ship / "
            "Self Ship fulfilment cost, GST on fees, return reserve, total cost, profit, margin, ROI, "
            "break-even price and a recommended selling price, with a beginner-friendly explanation."
        ),
    )
    async def _calculate_profitability(
        selling_price: float,
        product_cost: float,
        packaging_cost: float = 0.0,
        weight_grams: float = 250.0,
        fulfillment_method: str = "FBA",
        category: str = "Home & Kitchen",
        expected_return_rate: float = 0.05,
        other_costs: float = 0.0,
        shipping_cost_override: float | None = None,
    ) -> dict[str, Any]:
        return await calculate_profitability(
            services,
            selling_price,
            product_cost,
            packaging_cost,
            weight_grams,
            fulfillment_method,
            category,
            expected_return_rate,
            other_costs,
            shipping_cost_override,
        )
