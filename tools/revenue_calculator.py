"""MCP tool: ``calculate_revenue`` - monthly and annual revenue for a listing."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from tools import ServiceBundle, tool_handler

if TYPE_CHECKING:  # pragma: no cover
    from tools import MCPServerType

logger = logging.getLogger(__name__)


class RevenueInput(BaseModel):
    """Input schema for ``calculate_revenue``."""

    price: float = Field(gt=0, le=500_000)
    units_per_month: int | None = Field(default=None, ge=0)
    bsr: int | None = Field(default=None, gt=0)
    bought_past_month: int | None = Field(default=None, ge=0)
    category: str = Field(default="Home & Kitchen")
    product_cost: float | None = Field(default=None, ge=0)
    weight_grams: float = Field(default=250.0, gt=0)
    fulfillment_method: str = Field(default="FBA")


@tool_handler
async def calculate_revenue(
    services: ServiceBundle,
    price: float,
    units_per_month: int | None = None,
    bsr: int | None = None,
    bought_past_month: int | None = None,
    category: str = "Home & Kitchen",
    product_cost: float | None = None,
    weight_grams: float = 250.0,
    fulfillment_method: str = "FBA",
) -> dict[str, Any]:
    """Estimate revenue from units, a BSR, or Amazon's 'bought in past month' badge."""
    payload = RevenueInput(
        price=price,
        units_per_month=units_per_month,
        bsr=bsr,
        bought_past_month=bought_past_month,
        category=category,
        product_cost=product_cost,
        weight_grams=weight_grams,
        fulfillment_method=fulfillment_method,
    )

    # With a product cost we can turn gross revenue into take-home profit.
    net_per_unit = None
    profit_detail: dict[str, Any] | None = None
    if payload.product_cost is not None:
        breakdown = services.pricing.calculate(
            selling_price=payload.price,
            product_cost=payload.product_cost,
            weight_grams=payload.weight_grams,
            fulfillment_method=payload.fulfillment_method,
            category=payload.category,
        )
        net_per_unit = breakdown.estimated_profit
        profit_detail = {
            "profit_per_unit": breakdown.estimated_profit,
            "profit_margin_percent": round(breakdown.profit_margin * 100, 2),
            "total_cost_per_unit": breakdown.total_cost,
            "fee_data_type": services.pricing.envelope().data_type.value,
        }

    result = services.revenue.calculate_revenue(
        price=payload.price,
        units_per_month=payload.units_per_month,
        bsr=payload.bsr,
        bought_past_month=payload.bought_past_month,
        category=payload.category,
        net_profit_per_unit=net_per_unit,
    )
    if profit_detail:
        result["unit_economics"] = profit_detail
    result["how_to_read_this"] = [
        "'bought in past month' is Amazon's own published figure and is a floor, not an exact count.",
        "BSR-derived units are modelled from a power curve - use the range, not the midpoint.",
        "Revenue is gross unless a product_cost was supplied; it is not take-home profit.",
    ]
    result["caveat"] = "Sales are never guaranteed. These are planning figures, not a forecast."
    return result


def register(mcp: "MCPServerType", services: ServiceBundle) -> None:
    """Register ``calculate_revenue`` with the MCP server."""

    @mcp.tool(
        name="calculate_revenue",
        description=(
            "Estimate monthly and annual revenue for an Amazon India listing from units sold, a "
            "best-seller rank, or Amazon's 'X bought in past month' badge. Supply product_cost to also "
            "get monthly and annual profit. Returns a range plus the method used, never a single "
            "false-precision number."
        ),
    )
    async def _calculate_revenue(
        price: float,
        units_per_month: int | None = None,
        bsr: int | None = None,
        bought_past_month: int | None = None,
        category: str = "Home & Kitchen",
        product_cost: float | None = None,
        weight_grams: float = 250.0,
        fulfillment_method: str = "FBA",
    ) -> dict[str, Any]:
        return await calculate_revenue(
            services, price, units_per_month, bsr, bought_past_month, category,
            product_cost, weight_grams, fulfillment_method,
        )
