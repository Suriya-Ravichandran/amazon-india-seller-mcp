"""MCP tool: ``analyze_product_demand`` - demand level, trend and launch call."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from database.models import DemandAnalysis, save_record
from services import InsufficientDataError, recommendation_for_score
from tools import ServiceBundle, tool_handler

if TYPE_CHECKING:  # pragma: no cover
    from tools import MCPServerType

logger = logging.getLogger(__name__)


class DemandAnalysisInput(BaseModel):
    """Input schema for ``analyze_product_demand``."""

    product_name: str = Field(min_length=2, max_length=200)
    marketplace: str = Field(default="amazon.in")


@tool_handler
async def analyze_product_demand(
    services: ServiceBundle, product_name: str, marketplace: str = "amazon.in"
) -> dict[str, Any]:
    """Estimate demand, trend direction and seasonality, then give a launch call."""
    payload = DemandAnalysisInput(product_name=product_name, marketplace=marketplace)
    marketplace = services.amazon.validate_marketplace(payload.marketplace)

    # Marketplace signals sharpen the demand estimate, but are optional.
    snapshot = None
    try:
        snapshot = await services.amazon.get_product_snapshot(payload.product_name, marketplace)
    except InsufficientDataError:
        logger.info("No marketplace snapshot for %s; using keyword-only demand model", payload.product_name)

    demand = await services.trends.analyze_demand(payload.product_name, marketplace, snapshot)
    decision, reasons = _launch_decision(demand, snapshot)

    result = {
        "product_name": payload.product_name,
        "marketplace": marketplace,
        "estimated_monthly_demand": demand["estimated_monthly_demand_units"],
        "estimated_monthly_search_interest": demand["estimated_monthly_search_interest"],
        "demand_level": demand["demand_level"],
        "demand_score": demand["demand_score"],
        "trend_direction": demand["trend_direction"],
        "trend_note": demand["trend_note"],
        "seasonality": demand["seasonality"],
        "seasonality_risk": demand["seasonality_risk"],
        "confidence": demand["confidence"],
        "recommended_launch_decision": decision,
        "decision_reasons": reasons,
        "signals_used": demand["signals_used"],
        "marketplace_context": (
            {
                "average_price": snapshot.price_avg,
                "average_bsr": snapshot.bsr,
                "competitors_sampled": snapshot.competitor_count,
                "average_review_count": snapshot.review_count,
            }
            if snapshot
            else "No marketplace snapshot available; demand is keyword-modelled only."
        ),
        "caveat": (
            "Monthly demand is a model estimate, not measured Amazon sales. Never treat it as guaranteed volume."
        ),
        "source": demand["source"],
        "data_type": demand["data_type"],
        "last_updated": demand["last_updated"],
        "notes": demand["notes"],
    }

    await asyncio.to_thread(
        save_record,
        DemandAnalysis,
        product_name=payload.product_name,
        marketplace=marketplace,
        research_data=result,
        data_source=demand["source"],
        data_type=demand["data_type"],
        confidence=demand["confidence"],
        notes=demand["notes"],
        demand_level=demand["demand_level"],
        demand_score=demand["demand_score"],
        trend_direction=demand["trend_direction"],
        seasonality_risk=demand["seasonality_risk"],
    )
    return result


def _launch_decision(demand: dict[str, Any], snapshot: Any) -> tuple[str, list[str]]:
    """Blend demand, trend and seasonality into one of the five launch verdicts."""
    score = float(demand["demand_score"])
    reasons = [f"Demand score {score:.0f}/100 ({demand['demand_level']})."]

    if demand["trend_direction"] == "Rising":
        score += 6
        reasons.append("Search interest trend is rising.")
    elif demand["trend_direction"] == "Declining":
        score -= 12
        reasons.append("Search interest trend is declining - a real risk for a new launch.")

    risk = demand["seasonality_risk"]
    if risk in {"High", "Very High"}:
        score -= 15
        reasons.append(f"Seasonality risk is {risk}; demand concentrates in {', '.join(demand['seasonality']['peak_months'])}.")
    else:
        reasons.append("Demand looks year-round rather than seasonal.")

    if snapshot and snapshot.review_count > 3_000:
        score -= 5
        reasons.append("Competitors already hold heavy review counts, raising the entry barrier.")

    score = max(0.0, min(100.0, score))
    decision = recommendation_for_score(score)
    reasons.append(f"Adjusted launch score {score:.0f}/100 -> {decision}.")
    return decision, reasons


def register(mcp: "MCPServerType", services: ServiceBundle) -> None:
    """Register ``analyze_product_demand`` with the MCP server."""

    @mcp.tool(
        name="analyze_product_demand",
        description=(
            "Estimate monthly demand, demand level, trend direction and seasonality for a product on "
            "Amazon India, and return a launch decision (Strong Opportunity to Avoid). Estimates are "
            "modelled, never measured Amazon sales data."
        ),
    )
    async def _analyze_product_demand(product_name: str, marketplace: str = "amazon.in") -> dict[str, Any]:
        return await analyze_product_demand(services, product_name, marketplace)
