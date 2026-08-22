"""MCP tool: ``analyze_competitors`` - who you are up against, listing by listing."""

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


class CompetitorInput(BaseModel):
    """Input schema for ``analyze_competitors``."""

    keyword: str = Field(min_length=2, max_length=200)
    marketplace: str = Field(default="amazon.in")
    max_competitors: int = Field(default=20, ge=1, le=60)
    min_monthly_units: int | None = Field(default=None, ge=0)


@tool_handler
async def analyze_competitors(
    services: ServiceBundle,
    keyword: str,
    marketplace: str = "amazon.in",
    max_competitors: int = 20,
    min_monthly_units: int | None = None,
) -> dict[str, Any]:
    """Profile each competitor: sales volume, revenue, seller stage and entry difficulty.

    Two questions drive this tool: are any of these sellers *new* (proof a
    newcomer can rank), and do enough of them clear a real sales bar to make the
    keyword worth entering?
    """
    payload = CompetitorInput(
        keyword=keyword,
        marketplace=marketplace,
        max_competitors=max_competitors,
        min_monthly_units=min_monthly_units,
    )
    marketplace = services.amazon.validate_marketplace(payload.marketplace)

    listings = await services.amazon.search_products(payload.keyword, marketplace, payload.max_competitors)
    category = listings[0].category if listings else "default"

    if payload.min_monthly_units is not None:
        services.revenue.settings.min_monthly_units_target = payload.min_monthly_units

    rows = [
        {
            "title": item.title,
            "asin": item.asin,
            "brand": item.brand,
            "price": item.price,
            "rating": item.rating,
            "review_count": item.review_count,
            "bought_past_month": item.bought_past_month,
            "bsr": item.bsr,
        }
        for item in listings
    ]
    analysis = services.revenue.analyze_competitor_field(rows, category)
    competitors = analysis["competitors"]

    analysis.update(
        {
            "keyword": payload.keyword,
            "marketplace": marketplace,
            "category": category,
            "new_seller_opportunities": [
                {
                    "title": row["title"],
                    "asin": row["asin"],
                    "review_count": row["review_count"],
                    "estimated_monthly_units": row["estimated_monthly_units"],
                    "estimated_monthly_revenue": row["estimated_monthly_revenue"],
                    "why_it_matters": (
                        "A listing with few reviews is already selling well here - the page is winnable."
                    ),
                }
                for row in competitors
                if row["is_new_seller"] and row["meets_volume_target"]
            ][:5],
            "top_performers": sorted(competitors, key=lambda row: -row["estimated_monthly_revenue"])[:5],
            "data_quality": {
                "listings_with_bought_badge": sum(1 for row in competitors if row["bought_past_month"]),
                "listings_with_bsr": sum(1 for row in competitors if row["bsr"]),
                "units_from_amazon_badge": sum(
                    1 for row in competitors if row["units_method"] == "bought_in_past_month_badge"
                ),
                "note": (
                    "Listings carrying Amazon's own 'bought in past month' badge give a far better "
                    "volume estimate than a BSR curve. Run scrape_amazon_search for live badges."
                ),
            },
        }
    )

    await asyncio.to_thread(
        save_record,
        CompetitionAnalysis,
        product_name=payload.keyword,
        marketplace=marketplace,
        research_data=analysis,
        data_source=analysis["source"],
        data_type=analysis["data_type"],
        confidence=analysis["confidence"],
        notes=analysis.get("notes"),
        keyword=payload.keyword,
        competition_level=analysis["entry_verdict"][:32],
        competitors_analysed=analysis["competitors_analysed"],
        average_price=analysis["price_band"]["median"],
    )
    return analysis


def register(mcp: "MCPServerType", services: ServiceBundle) -> None:
    """Register ``analyze_competitors`` with the MCP server."""

    @mcp.tool(
        name="analyze_competitors",
        description=(
            "Profile every competitor for a keyword on Amazon India: estimated monthly units and "
            "revenue, market share, market size and concentration. Flags which competitors are NEW "
            "sellers (low review count - proof a newcomer can rank) and which clear a minimum monthly "
            "sales bar (300 units by default), then gives an entry verdict."
        ),
    )
    async def _analyze_competitors(
        keyword: str,
        marketplace: str = "amazon.in",
        max_competitors: int = 20,
        min_monthly_units: int | None = None,
    ) -> dict[str, Any]:
        return await analyze_competitors(services, keyword, marketplace, max_competitors, min_monthly_units)
