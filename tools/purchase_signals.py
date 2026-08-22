"""MCP tool: ``analyze_purchase_signals`` - Amazon's 'bought in past month' data."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from tools import ServiceBundle, tool_handler

if TYPE_CHECKING:  # pragma: no cover
    from tools import MCPServerType

logger = logging.getLogger(__name__)


class PurchaseSignalInput(BaseModel):
    """Input schema for ``analyze_purchase_signals``."""

    keyword: str = Field(min_length=2, max_length=200)
    marketplace: str = Field(default="amazon.in")
    max_listings: int = Field(default=20, ge=1, le=60)


@tool_handler
async def analyze_purchase_signals(
    services: ServiceBundle, keyword: str, marketplace: str = "amazon.in", max_listings: int = 20
) -> dict[str, Any]:
    """Aggregate Amazon's own '500+ bought in past month' badges across a keyword.

    That badge is the most reliable public sales signal Amazon publishes: it is
    Amazon's own number, not a model. It appears only on faster-moving listings,
    so its absence means "not shown", never "zero sales".
    """
    payload = PurchaseSignalInput(keyword=keyword, marketplace=marketplace, max_listings=max_listings)
    marketplace = services.amazon.validate_marketplace(payload.marketplace)

    listings = await services.amazon.search_products(payload.keyword, marketplace, payload.max_listings)
    with_badge = [item for item in listings if item.bought_past_month]
    total_units = sum(item.bought_past_month or 0 for item in with_badge)
    revenue = sum((item.bought_past_month or 0) * item.price for item in with_badge)
    target = services.settings.min_monthly_units_target
    hitting_target = [item for item in with_badge if (item.bought_past_month or 0) >= target]

    signals = [
        {
            "title": item.title,
            "asin": item.asin,
            "price": item.price,
            "bought_past_month": item.bought_past_month,
            "review_count": item.review_count,
            "estimated_monthly_revenue": round((item.bought_past_month or 0) * item.price, 2),
            "meets_volume_target": (item.bought_past_month or 0) >= target,
            "is_new_seller": item.review_count <= services.settings.new_seller_review_threshold,
        }
        for item in sorted(with_badge, key=lambda x: -(x.bought_past_month or 0))
    ]

    coverage = len(with_badge) / len(listings) if listings else 0.0
    return {
        "keyword": payload.keyword,
        "marketplace": marketplace,
        "listings_checked": len(listings),
        "listings_with_purchase_badge": len(with_badge),
        "badge_coverage_percent": round(coverage * 100, 1),
        "total_units_bought_past_month": total_units or None,
        "estimated_monthly_revenue_from_badges": round(revenue, 2) if revenue else None,
        "listings_meeting_volume_target": len(hitting_target),
        "volume_target": target,
        "top_selling_listings": signals[:10],
        "demand_verdict": _verdict(len(with_badge), len(hitting_target), total_units, target),
        "how_to_read_this": [
            "The badge is Amazon's own published figure - the best free sales signal available.",
            "It is a floor: '500+ bought' means at least 500, possibly far more.",
            "No badge does NOT mean no sales; Amazon shows it only on faster-moving listings.",
            "Totals cover only the listings sampled, not the whole category.",
        ],
        **services.amazon.provider.envelope(
            "Purchase badges are read from the listing data returned by the configured provider."
        ).as_dict(),
    }


def _verdict(with_badge: int, hitting_target: int, total_units: int, target: int) -> str:
    if not with_badge:
        return (
            "No purchase badges found. Either demand is thin, or this data source does not expose the "
            "badge - try scrape_amazon_search for live badge data."
        )
    if hitting_target >= 3:
        return (
            f"Strong, proven demand: {hitting_target} listings each move at least {target} units/month, "
            f"{total_units:,}+ units across the sampled page."
        )
    if hitting_target:
        return (
            f"Demand is real but concentrated: {hitting_target} listing(s) clear {target} units/month "
            f"out of {with_badge} showing a badge."
        )
    return (
        f"{with_badge} listings show purchase badges but none clear {target} units/month - "
        "the keyword may be too small to build a business on."
    )


def register(mcp: "MCPServerType", services: ServiceBundle) -> None:
    """Register ``analyze_purchase_signals`` with the MCP server."""

    @mcp.tool(
        name="analyze_purchase_signals",
        description=(
            "Aggregate Amazon India's 'X bought in past month' badges across a keyword: how many "
            "listings show one, total units, implied revenue, which listings clear a minimum monthly "
            "sales bar, and an overall demand verdict. The badge is Amazon's own published figure, "
            "making it the most reliable free sales signal available."
        ),
    )
    async def _analyze_purchase_signals(
        keyword: str, marketplace: str = "amazon.in", max_listings: int = 20
    ) -> dict[str, Any]:
        return await analyze_purchase_signals(services, keyword, marketplace, max_listings)
