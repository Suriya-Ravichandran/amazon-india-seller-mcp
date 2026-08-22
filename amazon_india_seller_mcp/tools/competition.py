"""MCP tool: ``analyze_competition`` - competitive landscape for a keyword."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from amazon_india_seller_mcp.database.models import CompetitionAnalysis, save_record
from amazon_india_seller_mcp.tools import ServiceBundle, tool_handler

if TYPE_CHECKING:  # pragma: no cover
    from amazon_india_seller_mcp.tools import MCPServerType

logger = logging.getLogger(__name__)


class CompetitionInput(BaseModel):
    """Input schema for ``analyze_competition``."""

    keyword: str = Field(min_length=2, max_length=200)
    marketplace: str = Field(default="amazon.in")
    max_competitors: int = Field(default=20, ge=1, le=100)


@tool_handler
async def analyze_competition(
    services: ServiceBundle,
    keyword: str,
    marketplace: str = "amazon.in",
    max_competitors: int = 20,
) -> dict[str, Any]:
    """Analyse competition strength and the gaps a new seller can exploit."""
    payload = CompetitionInput(keyword=keyword, marketplace=marketplace, max_competitors=max_competitors)

    analysis = await services.amazon.analyze_competition(
        payload.keyword, payload.marketplace, payload.max_competitors
    )
    analysis["entry_difficulty_summary"] = _entry_summary(analysis)

    await asyncio.to_thread(
        save_record,
        CompetitionAnalysis,
        product_name=payload.keyword,
        marketplace=analysis["marketplace"],
        research_data=analysis,
        data_source=analysis["source"],
        data_type=analysis["data_type"],
        confidence=analysis["confidence"],
        notes=analysis.get("notes"),
        keyword=payload.keyword,
        competition_level=analysis["competition_level"],
        competitors_analysed=analysis["competitors_analysed"],
        average_price=analysis["average_competitor_price"],
    )
    return analysis


def _entry_summary(analysis: dict[str, Any]) -> str:
    """One-paragraph read on how hard this keyword is to enter."""
    level = analysis["competition_level"]
    reviews = analysis["review_barrier"]["median_reviews_to_compete"]
    brand = analysis["brand_dominance"]["level"]
    quality = analysis["listing_quality"]["level"]
    return (
        f"Competition is {level}. A new listing needs roughly {reviews:,} reviews to look credible next to the "
        f"median competitor, brand dominance is {brand} and average listing quality is {quality}. "
        f"{analysis['differentiation_opportunity']}"
    )


def register(mcp: "MCPServerType", services: ServiceBundle) -> None:
    """Register ``analyze_competition`` with the MCP server."""

    @mcp.tool(
        name="analyze_competition",
        description=(
            "Analyse the Amazon India competitive landscape for a keyword: competition level, price and "
            "rating averages, review barrier, brand dominance, listing and image quality, weak listings, "
            "and bundle / differentiation / keyword opportunities."
        ),
    )
    async def _analyze_competition(
        keyword: str, marketplace: str = "amazon.in", max_competitors: int = 20
    ) -> dict[str, Any]:
        return await analyze_competition(services, keyword, marketplace, max_competitors)
