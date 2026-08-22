"""MCP tool: ``plan_product_launch`` - turn a product decision into a budget."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from amazon_india_seller_mcp.tools import ServiceBundle, tool_handler

if TYPE_CHECKING:  # pragma: no cover
    from amazon_india_seller_mcp.tools import MCPServerType

logger = logging.getLogger(__name__)

# Share of the total budget held back rather than spent on stock.
ADS_BUDGET_SHARE = 0.18
BUFFER_SHARE = 0.10
PHOTOGRAPHY_COST = 2_500.0
SAMPLE_COST_MULTIPLIER = 3


class LaunchPlanInput(BaseModel):
    """Input schema for ``plan_product_launch``."""

    product_name: str = Field(min_length=2, max_length=200)
    selling_price: float = Field(gt=0, le=500_000)
    product_cost: float = Field(gt=0)
    total_budget: float = Field(default=20_000, gt=0)
    packaging_cost: float = Field(default=15.0, ge=0)
    weight_grams: float = Field(default=250.0, gt=0)
    fulfillment_method: str = Field(default="FBA")
    category: str = Field(default="Home & Kitchen")
    expected_daily_sales: int = Field(default=5, ge=1, le=1000)


@tool_handler
async def plan_product_launch(
    services: ServiceBundle,
    product_name: str,
    selling_price: float,
    product_cost: float,
    total_budget: float = 20_000,
    packaging_cost: float = 15.0,
    weight_grams: float = 250.0,
    fulfillment_method: str = "FBA",
    category: str = "Home & Kitchen",
    expected_daily_sales: int = 5,
) -> dict[str, Any]:
    """Turn a product decision into an order quantity, budget split and timeline.

    Answers the question a beginner actually has after research: *how many units
    do I buy, what does the rest of the money go on, and when do I break even?*
    """
    payload = LaunchPlanInput(
        product_name=product_name,
        selling_price=selling_price,
        product_cost=product_cost,
        total_budget=total_budget,
        packaging_cost=packaging_cost,
        weight_grams=weight_grams,
        fulfillment_method=fulfillment_method,
        category=category,
        expected_daily_sales=expected_daily_sales,
    )

    breakdown = services.pricing.calculate(
        selling_price=payload.selling_price,
        product_cost=payload.product_cost,
        packaging_cost=payload.packaging_cost,
        weight_grams=payload.weight_grams,
        fulfillment_method=payload.fulfillment_method,
        category=payload.category,
    )

    # Split the budget before sizing the order: ads and a buffer come off the top.
    ads_budget = round(payload.total_budget * ADS_BUDGET_SHARE, 2)
    buffer = round(payload.total_budget * BUFFER_SHARE, 2)
    setup_costs = round(PHOTOGRAPHY_COST + payload.product_cost * SAMPLE_COST_MULTIPLIER, 2)
    inventory_budget = round(payload.total_budget - ads_budget - buffer - setup_costs, 2)

    unit_cash = payload.product_cost + payload.packaging_cost
    units = int(inventory_budget // unit_cash) if inventory_budget > 0 and unit_cash else 0
    inventory_spend = round(units * unit_cash, 2)

    monthly_sales = payload.expected_daily_sales * 30
    days_of_cover = round(units / payload.expected_daily_sales, 1) if payload.expected_daily_sales else 0
    monthly_profit = round(breakdown.estimated_profit * monthly_sales, 2)
    ads_per_order = round(breakdown.estimated_profit * 0.4, 2)
    profit_after_ads = round(breakdown.estimated_profit - ads_per_order, 2)
    payback_months = (
        round(payload.total_budget / (profit_after_ads * monthly_sales), 1)
        if profit_after_ads > 0 and monthly_sales
        else None
    )

    warnings = _warnings(units, days_of_cover, breakdown, inventory_budget, payload)

    return {
        "product_name": payload.product_name,
        "currency": "INR",
        "unit_economics": {
            "selling_price": breakdown.selling_price,
            "total_cost_per_unit": breakdown.total_cost,
            "profit_per_unit": breakdown.estimated_profit,
            "profit_margin_percent": round(breakdown.profit_margin * 100, 2),
            "break_even_price": breakdown.break_even_price,
        },
        "budget_allocation": {
            "total_budget": payload.total_budget,
            "inventory": inventory_spend,
            "setup_samples_and_photography": setup_costs,
            "advertising": ads_budget,
            "buffer": buffer,
            "unallocated": round(payload.total_budget - inventory_spend - setup_costs - ads_budget - buffer, 2),
        },
        "first_order": {
            "recommended_units": units,
            "cash_per_unit": round(unit_cash, 2),
            "inventory_investment": inventory_spend,
            "days_of_cover_at_expected_rate": days_of_cover,
            "reorder_when_stock_reaches": max(1, int(payload.expected_daily_sales * 21)),
            "reorder_note": "Assumes about 21 days from purchase order to Amazon FC availability.",
        },
        "advertising_plan": {
            "budget": ads_budget,
            "affordable_cost_per_order": ads_per_order,
            "profit_after_ads_per_unit": profit_after_ads,
            "daily_budget_first_month": round(ads_budget / 30, 2),
            "orders_ads_can_fund": int(ads_budget / ads_per_order) if ads_per_order > 0 else 0,
            "note": "Cap ad spend at ~40% of unit profit so the launch stays cash positive.",
        },
        "projection": {
            "expected_daily_sales": payload.expected_daily_sales,
            "expected_monthly_sales": monthly_sales,
            "gross_monthly_revenue": round(payload.selling_price * monthly_sales, 2),
            "monthly_profit_before_ads": monthly_profit,
            "monthly_profit_after_ads": round(profit_after_ads * monthly_sales, 2),
            "months_to_recover_budget": payback_months,
            "caveat": (
                "Sales are an assumption you supplied, not a forecast. Validate with "
                "analyze_purchase_signals before committing cash."
            ),
        },
        "timeline": [
            {"week": "1", "action": "Order samples from 3 suppliers; verify GSTIN and material quality."},
            {"week": "2", "action": "Pick the supplier, negotiate, place the first order, book photography."},
            {"week": "3-4", "action": "Prepare listing copy and images; complete Amazon seller registration."},
            {"week": "5-6", "action": "Stock arrives; label FNSKU and ship into the FC."},
            {"week": "7", "action": "Go live; start ads at the daily budget above; target 5-10 reviews."},
            {"week": "8-12", "action": "Optimise on search-term reports; reorder at the trigger stock level."},
        ],
        "warnings": warnings,
        "readiness_verdict": _verdict(units, warnings, breakdown),
        **services.pricing.envelope().as_dict(),
    }


def _warnings(
    units: int, days_of_cover: float, breakdown: Any, inventory_budget: float, payload: LaunchPlanInput
) -> list[str]:
    warnings: list[str] = []
    if inventory_budget <= 0:
        warnings.append(
            "The budget does not cover setup costs and stock. Raise the budget or cut the sample/photography spend."
        )
    if units and units < 50:
        warnings.append(
            f"Only {units} units affordable. Most Indian suppliers will not quote well below 50-100 units."
        )
    if breakdown.profit_margin < 0.30:
        warnings.append(
            f"Margin is {breakdown.profit_margin * 100:.1f}%, under the 30% target - little room for ads or returns."
        )
    if days_of_cover and days_of_cover < 30:
        warnings.append(
            f"Only {days_of_cover:.0f} days of cover; with a ~21 day lead time you will stock out. Order more or sell slower."
        )
    if payload.weight_grams > 500:
        warnings.append("Over 500 g pushes you into a higher fulfilment slab and out of the beginner brief.")
    if breakdown.estimated_profit <= 0:
        warnings.append("This product loses money per order. Do not launch it.")
    return warnings


def _verdict(units: int, warnings: list[str], breakdown: Any) -> str:
    if breakdown.estimated_profit <= 0:
        return "Not launchable: the unit economics are negative."
    if not warnings:
        return f"Ready to launch: {units} units, healthy margin, and budget left for ads and a buffer."
    if len(warnings) == 1:
        return f"Launchable with one caveat: {warnings[0]}"
    return f"Not ready yet: {len(warnings)} issues to resolve before committing cash."


def register(mcp: "MCPServerType", services: ServiceBundle) -> None:
    """Register ``plan_product_launch`` with the MCP server."""

    @mcp.tool(
        name="plan_product_launch",
        description=(
            "Turn a product decision into a launch plan for Amazon India: how many units to order, how "
            "to split the budget across inventory, samples, photography, ads and buffer, days of stock "
            "cover, reorder trigger, affordable ad cost per order, months to recover the budget, a "
            "week-by-week timeline, and warnings before you commit cash."
        ),
    )
    async def _plan_product_launch(
        product_name: str,
        selling_price: float,
        product_cost: float,
        total_budget: float = 20_000,
        packaging_cost: float = 15.0,
        weight_grams: float = 250.0,
        fulfillment_method: str = "FBA",
        category: str = "Home & Kitchen",
        expected_daily_sales: int = 5,
    ) -> dict[str, Any]:
        return await plan_product_launch(
            services, product_name, selling_price, product_cost, total_budget, packaging_cost,
            weight_grams, fulfillment_method, category, expected_daily_sales,
        )
