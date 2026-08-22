"""MCP tool: ``generate_listing`` - an Amazon India ready listing draft."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from amazon_india_seller_mcp.tools import ServiceBundle, tool_handler

if TYPE_CHECKING:  # pragma: no cover
    from amazon_india_seller_mcp.tools import MCPServerType

logger = logging.getLogger(__name__)


class ListingInput(BaseModel):
    """Input schema for ``generate_listing``."""

    product_name: str = Field(min_length=2, max_length=200)
    features: list[str] = Field(min_length=1, max_length=10)
    target_keywords: list[str] = Field(default_factory=list, max_length=20)
    target_market: str = Field(default="India")


@tool_handler
async def generate_listing(
    services: ServiceBundle,
    product_name: str,
    features: list[str],
    target_keywords: list[str] | None = None,
    target_market: str = "India",
) -> dict[str, Any]:
    """Generate title, bullets, description, backend terms and image direction."""
    payload = ListingInput(
        product_name=product_name,
        features=features,
        target_keywords=target_keywords or [],
        target_market=target_market,
    )

    keywords = payload.target_keywords
    if not keywords:
        # Fall back to researched keywords so the listing is never keyword-blind.
        research = await services.trends.research_keywords(payload.product_name)
        keywords = research["primary_keywords"] + research["secondary_keywords"][:3]

    listing = services.amazon.build_listing(
        payload.product_name, payload.features, keywords, payload.target_market
    )
    listing["compliance_checklist"] = [
        "No promotional text ('best seller', 'sale', 'free shipping') in the title or images.",
        "No contact details, URLs or review requests inside the listing copy.",
        "Do not claim BPA-free, food grade, medical or warranty benefits without supplier documentation.",
        "Title under 200 characters; keep the key benefit inside the first 80 characters for mobile.",
        "Backend search terms under 250 bytes with no repetition of title words.",
    ]
    return listing


def register(mcp: "MCPServerType", services: ServiceBundle) -> None:
    """Register ``generate_listing`` with the MCP server."""

    @mcp.tool(
        name="generate_listing",
        description=(
            "Generate an Amazon India listing: SEO title plus alternatives, five benefit-led bullet points, "
            "product description, backend search terms, keyword placement strategy, main / lifestyle / "
            "infographic / comparison image direction, packaging advice and a compliance checklist."
        ),
    )
    async def _generate_listing(
        product_name: str,
        features: list[str],
        target_keywords: list[str] | None = None,
        target_market: str = "India",
    ) -> dict[str, Any]:
        return await generate_listing(services, product_name, features, target_keywords, target_market)
