"""MCP tool: ``search_web`` - free web search for product and supplier research."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from tools import ServiceBundle, tool_handler

if TYPE_CHECKING:  # pragma: no cover
    from tools import MCPServerType

logger = logging.getLogger(__name__)


class WebSearchInput(BaseModel):
    """Input schema for ``search_web``."""

    query: str = Field(min_length=2, max_length=300)
    max_results: int = Field(default=10, ge=1, le=50)
    region: str = Field(default="in-en", description="Search region, e.g. 'in-en' for India/English")


@tool_handler
async def search_web(
    services: ServiceBundle, query: str, max_results: int = 10, region: str = "in-en"
) -> dict[str, Any]:
    """Search the web through the configured provider (DuckDuckGo needs no API key)."""
    payload = WebSearchInput(query=query, max_results=max_results, region=region)

    result = await services.search.search(payload.query, payload.max_results, payload.region)
    result["provider_status"] = services.search.status()
    result["research_uses"] = [
        "Find which brands and sellers dominate a product outside Amazon.",
        "Locate supplier, wholesaler and B2B directory pages for a product.",
        "Compare prices across marketplaces before setting yours.",
        "Check whether a product idea is discussed, reviewed or trending anywhere.",
    ]
    return result


def register(mcp: "MCPServerType", services: ServiceBundle) -> None:
    """Register ``search_web`` with the MCP server."""

    @mcp.tool(
        name="search_web",
        description=(
            "Search the web for product, competitor, price and supplier research. Uses DuckDuckGo by "
            "default (free, no API key); Brave, Serper, Tavily and Google Programmable Search are "
            "supported when a key is configured. Returns live web results, not Amazon data."
        ),
    )
    async def _search_web(query: str, max_results: int = 10, region: str = "in-en") -> dict[str, Any]:
        return await search_web(services, query, max_results, region)
