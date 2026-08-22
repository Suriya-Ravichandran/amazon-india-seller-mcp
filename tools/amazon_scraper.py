"""MCP tools: live Amazon India page scraping, plus a status check.

Three tools live here:

* ``scrape_amazon_search`` - search results with prices, ratings, review counts
  and 'bought in past month' badges
* ``scrape_amazon_product`` - one product page: BSR, weight, images, seller
* ``scraper_status`` - what the browser layer is configured to do right now

Every fetch passes through :mod:`services.browser_service`, which enforces the
domain allowlist, robots.txt, crawl delay and page budget, and stops cleanly if
Amazon serves a bot challenge. This server does not attempt to defeat bot
protection; see ``docs/SCRAPING.md`` for what that means in practice.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from tools import ServiceBundle, tool_handler

if TYPE_CHECKING:  # pragma: no cover
    from tools import MCPServerType

logger = logging.getLogger(__name__)


class ScrapeSearchInput(BaseModel):
    """Input schema for ``scrape_amazon_search``."""

    keyword: str = Field(min_length=2, max_length=200)
    pages: int = Field(default=1, ge=1, le=5)
    render: bool = Field(default=False, description="Render with Chromium (slower, handles JS pages)")


class ScrapeProductInput(BaseModel):
    """Input schema for ``scrape_amazon_product``."""

    asin: str = Field(min_length=10, max_length=10)
    render: bool = Field(default=False)


@tool_handler
async def scrape_amazon_search(
    services: ServiceBundle, keyword: str, pages: int = 1, render: bool = False
) -> dict[str, Any]:
    """Scrape live Amazon India search results for a keyword."""
    payload = ScrapeSearchInput(keyword=keyword, pages=pages, render=render)
    result = await services.scraper.scrape_search(payload.keyword, payload.pages, payload.render)

    listings = result["listings"]
    with_badge = [row for row in listings if row.get("bought_past_month")]
    result["quick_read"] = {
        "listings_with_purchase_badge": len(with_badge),
        "total_units_bought_past_month": sum(row["bought_past_month"] for row in with_badge) or None,
        "price_range": _price_range(listings),
        "lowest_review_count": min((row["review_count"] for row in listings if row.get("review_count")), default=None),
        "sponsored_share": round(result["sponsored_listings"] / max(1, result["listings_found"]), 2),
    }
    result["next_steps"] = [
        "Feed these ASINs to scrape_amazon_product for BSR, weight and full image galleries.",
        "Run analyze_competitors on the same keyword to turn this into units, revenue and an entry verdict.",
    ]
    result["field_coverage_note"] = (
        "field_coverage shows how many listings each field parsed for. A low count means Amazon "
        "changed its markup - fix the selectors via BROWSER_SELECTORS_PATH rather than trusting gaps."
    )
    return result


@tool_handler
async def scrape_amazon_product(services: ServiceBundle, asin: str, render: bool = False) -> dict[str, Any]:
    """Scrape one live Amazon India product page by ASIN."""
    payload = ScrapeProductInput(asin=asin, render=render)
    result = await services.scraper.scrape_product(payload.asin, payload.render)

    product = result["product"]
    units = services.revenue.best_units_estimate(
        product.get("bought_past_month"), product.get("bsr"), product.get("bsr_category") or "default"
    )
    if units and product.get("price"):
        result["sales_estimate"] = {
            "estimated_monthly_units": units.units_per_month,
            "units_range": {"low": units.range_low, "high": units.range_high},
            "method": units.method,
            "basis": units.basis,
            "estimated_monthly_revenue": round(units.units_per_month * product["price"], 2),
            "confidence": units.confidence,
        }
    result["missing_fields"] = [key for key, value in product.items() if value in (None, [], "")]
    result["note"] = (
        "Fields that could not be parsed are returned as null, never as zero or a guess. "
        "A long missing_fields list usually means Amazon changed its markup."
    )
    return result


@tool_handler
async def scraper_status(services: ServiceBundle) -> dict[str, Any]:
    """Report how the scraping layer is configured and whether it can run."""
    status = services.browser.status()
    ready = status["browser_enabled"] and bool(status["allowed_domains"])
    return {
        "browser": status,
        "search_provider": services.search.status(),
        "product_data_provider": services.settings.product_data_provider,
        "google_trends_enabled": services.settings.google_trends_enabled,
        "demo_mode": services.settings.is_demo,
        "ready_to_scrape": ready,
        "blockers": _blockers(status),
        "compliance": {
            "robots_txt": "enforced" if status["respect_robots"] else "DISABLED - you have overridden this",
            "crawl_delay_seconds": status["min_delay_seconds"],
            "page_budget": status["max_pages_per_run"],
            "bot_protection_bypass": "not implemented by design",
            "note": (
                "amazon.in robots.txt currently permits /s, /dp, /product-reviews and /gp/bestsellers, "
                "but Amazon's Conditions of Use separately restrict automated data collection. "
                "A licensed data API (Rainforest, Keepa, SP-API) is the compliant route at scale."
            ),
        },
    }


def _blockers(status: dict[str, Any]) -> list[str]:
    blockers = []
    if not status["browser_enabled"]:
        blockers.append("BROWSER_ENABLED is false - set it to true in .env.")
    if not status["allowed_domains"]:
        blockers.append("BROWSER_ALLOWED_DOMAINS is empty - add 'amazon.in'.")
    if not status["playwright_available"]:
        blockers.append(
            "Playwright is not installed (only needed for render=true): "
            "`uv sync --extra browser` then `uv run playwright install chromium`."
        )
    return blockers


def _price_range(listings: list[dict[str, Any]]) -> dict[str, float] | None:
    prices = [row["price"] for row in listings if row.get("price")]
    return {"min": min(prices), "max": max(prices)} if prices else None


def register(mcp: "MCPServerType", services: ServiceBundle) -> None:
    """Register the scraping tools with the MCP server."""

    @mcp.tool(
        name="scrape_amazon_search",
        description=(
            "Scrape live Amazon India search results for a keyword: ASIN, title, price, rating, review "
            "count, 'bought in past month' badge, image and sponsored flag for each listing. Honours "
            "robots.txt, an allowlist, a crawl delay and a page budget, and stops if Amazon serves a "
            "bot challenge. Requires BROWSER_ENABLED=true and amazon.in in BROWSER_ALLOWED_DOMAINS."
        ),
    )
    async def _scrape_amazon_search(keyword: str, pages: int = 1, render: bool = False) -> dict[str, Any]:
        return await scrape_amazon_search(services, keyword, pages, render)

    @mcp.tool(
        name="scrape_amazon_product",
        description=(
            "Scrape one live Amazon India product page by ASIN: title, brand, price, rating, review "
            "count, best-seller ranks, weight, seller, bullet points, full image gallery and the "
            "'bought in past month' badge, plus a sales and revenue estimate derived from them."
        ),
    )
    async def _scrape_amazon_product(asin: str, render: bool = False) -> dict[str, Any]:
        return await scrape_amazon_product(services, asin, render)

    @mcp.tool(
        name="scraper_status",
        description=(
            "Check how the scraping and data layers are configured: browser enabled, allowlisted "
            "domains, robots.txt enforcement, crawl delay, page budget, Playwright availability, "
            "search provider, Google Trends status, and anything blocking a live scrape."
        ),
    )
    async def _scraper_status() -> dict[str, Any]:
        return await scraper_status(services)
