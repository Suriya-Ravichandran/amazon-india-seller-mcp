"""MCP tool: ``analyze_evergreen`` - is demand steady all year, or a spike?"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from services import DataType
from services.trends_service import synthetic_interest_series
from tools import ServiceBundle, tool_handler

if TYPE_CHECKING:  # pragma: no cover
    from tools import MCPServerType

logger = logging.getLogger(__name__)

TIMEFRAMES = {
    "1y": "today 12-m",
    "3y": "today 5-y",
    "5y": "today 5-y",
}


class EvergreenInput(BaseModel):
    """Input schema for ``analyze_evergreen``."""

    product_name: str = Field(min_length=2, max_length=200)
    years: str = Field(default="5y", description="Look-back window: 1y, 3y or 5y")
    geo: str = Field(default="IN", description="Two-letter region code for search interest")


@tool_handler
async def analyze_evergreen(
    services: ServiceBundle, product_name: str, years: str = "5y", geo: str = "IN"
) -> dict[str, Any]:
    """Judge whether a product has evergreen demand from its interest history.

    Uses real Google Trends data when ``GOOGLE_TRENDS_ENABLED=true`` (free, no
    API key); otherwise falls back to a modelled series that is labelled as
    such. An evergreen product is the safest first product for a beginner: no
    dead stock after a season ends.
    """
    payload = EvergreenInput(product_name=product_name, years=years, geo=geo)
    timeframe = TIMEFRAMES.get(payload.years.lower(), "today 5-y")

    trends = await services.trends.fetch_interest_over_time(payload.product_name, timeframe, payload.geo)
    if trends and len(trends["series"]) >= 6:
        series = trends["series"]
        source = "Google Trends (free, no API key)"
        data_type = DataType.LIVE
        dates = trends["dates"]
    else:
        series = synthetic_interest_series(payload.product_name, months=36)
        source = (
            "Internal seasonality model"
            if not services.settings.is_demo
            else "Local Demo Provider"
        )
        data_type = DataType.ESTIMATED if not services.settings.is_demo else DataType.DEMO
        dates = []

    analysis = services.revenue.evergreen_analysis(
        product_name=payload.product_name,
        monthly_interest=series,
        trend_direction=_direction(series),
        source=source,
        data_type=data_type,
    )
    analysis["timeframe"] = timeframe
    analysis["geo"] = payload.geo
    analysis["interest_series"] = series
    analysis["series_dates"] = dates
    analysis["beginner_guidance"] = _beginner_guidance(analysis)
    if data_type is not DataType.LIVE:
        analysis["upgrade_hint"] = (
            "Set GOOGLE_TRENDS_ENABLED=true and run `uv sync --extra realtime` to score this on real "
            "Google Trends data instead of a model. It is free and needs no API key."
        )
    return analysis


def _direction(series: list[int]) -> str:
    half = len(series) // 2 or 1
    earlier = sum(series[:half]) / half
    recent = sum(series[half:]) / max(1, len(series) - half)
    if earlier and recent > earlier * 1.15:
        return "Rising"
    if earlier and recent < earlier * 0.85:
        return "Declining"
    return "Stable"


def _beginner_guidance(analysis: dict[str, Any]) -> list[str]:
    """Turn the evergreen verdict into inventory and cash-flow advice."""
    if analysis["is_evergreen"]:
        return [
            "Safe to reorder on a fixed cycle - demand does not fall off a cliff.",
            "You can build reviews slowly without racing a seasonal window.",
            "Hold roughly 45-60 days of cover; you are unlikely to be left with dead stock.",
        ]
    if analysis["evergreen_score"] >= 45:
        return [
            "Time your first order to land 6-8 weeks before the peak months.",
            "Keep the off-season order small - cash is better spent on an evergreen SKU.",
            "Expect ad costs to spike as everyone bids into the same season.",
        ]
    return [
        "Risky as a first product: demand concentrates in a short window.",
        "Miss the window and stock sits for a year while storage fees accrue.",
        "Prefer a year-round daily-use product until you have cash flow to absorb the risk.",
    ]


def register(mcp: "MCPServerType", services: ServiceBundle) -> None:
    """Register ``analyze_evergreen`` with the MCP server."""

    @mcp.tool(
        name="analyze_evergreen",
        description=(
            "Decide whether a product has evergreen (year-round) demand or is seasonal / a fad, using "
            "up to 5 years of search interest. Returns an evergreen score 0-100, a verdict "
            "(Evergreen to Highly Seasonal), stability / flatness / demand-floor / growth components, "
            "and inventory guidance. Uses live Google Trends when enabled - free, no API key."
        ),
    )
    async def _analyze_evergreen(product_name: str, years: str = "5y", geo: str = "IN") -> dict[str, Any]:
        return await analyze_evergreen(services, product_name, years, geo)
