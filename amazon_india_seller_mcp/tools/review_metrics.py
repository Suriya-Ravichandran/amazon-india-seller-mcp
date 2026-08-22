"""MCP tool: ``analyze_review_metrics`` - review counts as a competitive barrier."""

from __future__ import annotations

import logging
import statistics
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from amazon_india_seller_mcp.tools import ServiceBundle, tool_handler

if TYPE_CHECKING:  # pragma: no cover
    from amazon_india_seller_mcp.tools import MCPServerType

logger = logging.getLogger(__name__)


class ReviewMetricsInput(BaseModel):
    """Input schema for ``analyze_review_metrics``."""

    keyword: str = Field(min_length=2, max_length=200)
    marketplace: str = Field(default="amazon.in")
    max_listings: int = Field(default=20, ge=1, le=60)


@tool_handler
async def analyze_review_metrics(
    services: ServiceBundle, keyword: str, marketplace: str = "amazon.in", max_listings: int = 20
) -> dict[str, Any]:
    """Measure the review barrier for a keyword: counts, spread and who is beatable.

    Review count is the clearest public proxy for how entrenched a listing is.
    A low count next to healthy sales is the single best sign that a page can
    still be taken by a new seller.
    """
    payload = ReviewMetricsInput(keyword=keyword, marketplace=marketplace, max_listings=max_listings)
    marketplace = services.amazon.validate_marketplace(payload.marketplace)

    listings = await services.amazon.search_products(payload.keyword, marketplace, payload.max_listings)
    counts = sorted(item.review_count for item in listings)
    ratings = [item.rating for item in listings if item.rating]
    threshold = services.settings.new_seller_review_threshold

    quartile = counts[len(counts) // 4] if counts else 0
    median = statistics.median(counts) if counts else 0
    beatable = [
        {
            "title": item.title,
            "asin": item.asin,
            "review_count": item.review_count,
            "rating": item.rating,
            "price": item.price,
            "bought_past_month": item.bought_past_month,
            "why_beatable": _why_beatable(item, threshold, median),
        }
        for item in sorted(listings, key=lambda x: x.review_count)
        if item.review_count <= max(threshold, quartile) or item.rating < 3.9
    ][:8]

    return {
        "keyword": payload.keyword,
        "marketplace": marketplace,
        "listings_analysed": len(listings),
        "review_counts": {
            "total_across_listings": sum(counts),
            "min": min(counts) if counts else 0,
            "max": max(counts) if counts else 0,
            "median": int(median),
            "mean": int(statistics.fmean(counts)) if counts else 0,
            "lower_quartile": int(quartile),
        },
        "rating": {
            "average": round(statistics.fmean(ratings), 2) if ratings else None,
            "min": min(ratings) if ratings else None,
            "max": max(ratings) if ratings else None,
            "listings_below_4_stars": sum(1 for rating in ratings if rating < 4.0),
        },
        "review_barrier": {
            "reviews_to_look_credible": int(quartile),
            "reviews_to_match_the_median": int(median),
            "level": "Very High" if median > 3000 else "High" if median > 1000 else "Medium" if median > 300 else "Low",
            "realistic_months_to_reach_median": _months_to_reach(median),
        },
        "new_seller_listings": {
            "threshold": threshold,
            "count": sum(1 for item in listings if item.review_count <= threshold),
            "share_percent": round(
                sum(1 for item in listings if item.review_count <= threshold) / len(listings) * 100, 1
            )
            if listings
            else 0.0,
        },
        "beatable_listings": beatable,
        "verdict": _verdict(median, quartile, len(beatable), threshold),
        "assumptions": [
            "Review velocity assumes roughly 1-2 reviews per 100 orders, typical for Indian marketplaces.",
            "Review counts are a proxy for entrenchment, not a direct measure of sales.",
        ],
        **services.amazon.provider.envelope(
            "Review counts come from the configured product data provider."
        ).as_dict(),
    }


def _why_beatable(item: Any, threshold: int, median: float) -> str:
    reasons = []
    if item.review_count <= threshold:
        reasons.append(f"only {item.review_count} reviews")
    elif item.review_count < median:
        reasons.append(f"{item.review_count} reviews, below the page median")
    if item.rating < 3.9:
        reasons.append(f"weak {item.rating} rating - unresolved complaints to fix")
    if item.bought_past_month:
        reasons.append(f"still selling ({item.bought_past_month}+/month), so demand is proven")
    return "; ".join(reasons) or "below-average position on the page"


def _months_to_reach(median: float) -> str:
    """Rough months to match the median review count at a 1.5% review rate."""
    if median <= 0:
        return "Not applicable"
    orders_needed = median / 0.015
    for units in (300, 600, 1000):
        months = orders_needed / units
        if months <= 24:
            return f"About {months:.0f} months at {units} orders/month (1.5% review rate)"
    return f"Over 2 years at 1,000 orders/month - {int(orders_needed):,} orders needed"


def _verdict(median: float, quartile: float, beatable: int, threshold: int) -> str:
    if median <= 300:
        return f"Low barrier: the median listing has only {int(median)} reviews. A new listing can compete within months."
    if beatable >= 3:
        return (
            f"Medium barrier: the median is {int(median)} reviews, but {beatable} listings are beatable "
            "on review count or rating."
        )
    if median > 3000:
        return (
            f"Very high barrier: the median listing holds {int(median):,} reviews. Expect a long, "
            "expensive climb - consider a narrower long-tail keyword instead."
        )
    return f"High barrier: {int(median):,} median reviews with few weak listings to target."


def register(mcp: "MCPServerType", services: ServiceBundle) -> None:
    """Register ``analyze_review_metrics`` with the MCP server."""

    @mcp.tool(
        name="analyze_review_metrics",
        description=(
            "Measure the review barrier for an Amazon India keyword: total, median, quartile and range "
            "of competitor review counts, rating spread, how many months it would take to match the "
            "median, which listings are beatable on reviews or rating, and how many are new sellers."
        ),
    )
    async def _analyze_review_metrics(
        keyword: str, marketplace: str = "amazon.in", max_listings: int = 20
    ) -> dict[str, Any]:
        return await analyze_review_metrics(services, keyword, marketplace, max_listings)
