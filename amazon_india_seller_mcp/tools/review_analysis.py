"""MCP tool: ``analyze_reviews`` - complaint clustering and improvement ideas."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from amazon_india_seller_mcp.tools import ServiceBundle, tool_handler

if TYPE_CHECKING:  # pragma: no cover
    from amazon_india_seller_mcp.tools import MCPServerType

logger = logging.getLogger(__name__)


class ReviewAnalysisInput(BaseModel):
    """Input schema for ``analyze_reviews``."""

    product_name: str = Field(min_length=2, max_length=200)
    marketplace: str = Field(default="amazon.in")
    max_reviews: int = Field(default=200, ge=10, le=1000)


@tool_handler
async def analyze_reviews(
    services: ServiceBundle,
    product_name: str,
    marketplace: str = "amazon.in",
    max_reviews: int = 200,
) -> dict[str, Any]:
    """Group customer reviews into complaint and praise themes with concrete fixes."""
    payload = ReviewAnalysisInput(
        product_name=product_name, marketplace=marketplace, max_reviews=max_reviews
    )

    analysis = await services.amazon.analyze_reviews(
        payload.product_name, payload.marketplace, payload.max_reviews
    )
    analysis["how_to_use_this"] = [
        "Fix the top complaint at the supplier stage - it is the cheapest place to fix it.",
        "Put the fix in bullet 1 and in a comparison infographic so buyers see the difference.",
        "Turn the most appreciated features into your main image and A+ content angles.",
    ]
    analysis["caveat"] = (
        "Themes are clustered from review text by keyword matching. Read the actual reviews of the top "
        "listings before committing money to a product change."
    )
    return analysis


def register(mcp: "MCPServerType", services: ServiceBundle) -> None:
    """Register ``analyze_reviews`` with the MCP server."""

    @mcp.tool(
        name="analyze_reviews",
        description=(
            "Analyse customer reviews for a product on Amazon India: most common complaints grouped by theme "
            "with mention counts, most appreciated features, quality / packaging / size / usability problems, "
            "defects, and recommended product improvements and differentiation."
        ),
    )
    async def _analyze_reviews(
        product_name: str, marketplace: str = "amazon.in", max_reviews: int = 200
    ) -> dict[str, Any]:
        return await analyze_reviews(services, product_name, marketplace, max_reviews)
