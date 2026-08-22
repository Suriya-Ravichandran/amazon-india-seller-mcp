"""MCP tool: ``search_suppliers`` - sourcing research that never invents suppliers."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from database.models import SupplierResearch, save_record
from tools import ServiceBundle, tool_handler

if TYPE_CHECKING:  # pragma: no cover
    from tools import MCPServerType

logger = logging.getLogger(__name__)


class SupplierSearchInput(BaseModel):
    """Input schema for ``search_suppliers``."""

    product_name: str = Field(min_length=2, max_length=200)
    location: str = Field(default="India", description="Parrys, Chennai, Tamil Nadu or India")
    supplier_type: str = Field(default="any", description="Manufacturer, Wholesaler, Trader or Any")


@tool_handler
async def search_suppliers(
    services: ServiceBundle,
    product_name: str,
    location: str = "India",
    supplier_type: str = "any",
) -> dict[str, Any]:
    """Find suppliers, or - when no supplier API is configured - real sourcing channels."""
    payload = SupplierSearchInput(product_name=product_name, location=location, supplier_type=supplier_type)

    result = await services.supplier.search_suppliers(
        payload.product_name, payload.location, payload.supplier_type
    )
    result["how_to_read_this"] = (
        "'Verified' means the provider independently confirmed the supplier. 'Public Listing' means it is a "
        "publicly known market, cluster or directory you must still verify yourself. Nothing in this result is "
        "a recommendation or an endorsement of any seller."
    )

    await asyncio.to_thread(
        save_record,
        SupplierResearch,
        product_name=payload.product_name,
        marketplace="amazon.in",
        research_data=result,
        data_source=result["source"],
        data_type=result["data_type"],
        confidence=result["confidence"],
        notes=result.get("notes"),
        location=result["location"],
        supplier_type=result["supplier_type"],
        suppliers_found=len(result.get("suppliers") or []),
    )
    return result


def register(mcp: "MCPServerType", services: ServiceBundle) -> None:
    """Register ``search_suppliers`` with the MCP server."""

    @mcp.tool(
        name="search_suppliers",
        description=(
            "Research sourcing for a product in India (Parrys, Chennai, Tamil Nadu or nationwide). Returns "
            "supplier records only when a supplier data API is configured; otherwise returns real, publicly "
            "known wholesale markets, manufacturing clusters and B2B directories plus a verification checklist. "
            "Supplier names, prices and MOQs are never invented."
        ),
    )
    async def _search_suppliers(
        product_name: str, location: str = "India", supplier_type: str = "any"
    ) -> dict[str, Any]:
        return await search_suppliers(services, product_name, location, supplier_type)
