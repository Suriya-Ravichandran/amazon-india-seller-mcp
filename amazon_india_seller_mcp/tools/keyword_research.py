"""MCP tool: ``research_keywords`` - Amazon India keyword sets and placement plan."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from amazon_india_seller_mcp.tools import ServiceBundle, tool_handler

if TYPE_CHECKING:  # pragma: no cover
    from amazon_india_seller_mcp.tools import MCPServerType

logger = logging.getLogger(__name__)


class KeywordResearchInput(BaseModel):
    """Input schema for ``research_keywords``."""

    product_name: str = Field(min_length=2, max_length=200)
    marketplace: str = Field(default="amazon.in")


@tool_handler
async def research_keywords(
    services: ServiceBundle, product_name: str, marketplace: str = "amazon.in"
) -> dict[str, Any]:
    """Build primary, secondary, long-tail, related and backend keyword sets."""
    payload = KeywordResearchInput(product_name=product_name, marketplace=marketplace)
    marketplace = services.amazon.validate_marketplace(payload.marketplace)

    result = await services.trends.research_keywords(payload.product_name, marketplace)
    result["caveat"] = (
        "Search volumes and priorities are modelled estimates, not Amazon search-term report data. "
        "Validate them with a Brand Analytics or advertising search-term report once you are live."
    )
    return result


def register(mcp: "MCPServerType", services: ServiceBundle) -> None:
    """Register ``research_keywords`` with the MCP server."""

    @mcp.tool(
        name="research_keywords",
        description=(
            "Research Amazon India keywords for a product: primary, secondary, long-tail and related "
            "keywords, search intent, keyword priority, backend search terms, and where to place each "
            "keyword across title, bullets, description and backend fields."
        ),
    )
    async def _research_keywords(product_name: str, marketplace: str = "amazon.in") -> dict[str, Any]:
        return await research_keywords(services, product_name, marketplace)
