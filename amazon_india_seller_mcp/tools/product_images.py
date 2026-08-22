"""MCP tool: ``analyze_product_images`` - image gaps you can win on."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from amazon_india_seller_mcp.services import InvalidInputError
from amazon_india_seller_mcp.tools import ServiceBundle, tool_handler

if TYPE_CHECKING:  # pragma: no cover
    from amazon_india_seller_mcp.tools import MCPServerType

logger = logging.getLogger(__name__)

RECOMMENDED_IMAGE_COUNT = 7


class ProductImageInput(BaseModel):
    """Input schema for ``analyze_product_images``."""

    product_name: str | None = Field(default=None, max_length=200)
    asin: str | None = Field(default=None, max_length=10)
    marketplace: str = Field(default="amazon.in")
    max_listings: int = Field(default=10, ge=1, le=30)


@tool_handler
async def analyze_product_images(
    services: ServiceBundle,
    product_name: str | None = None,
    asin: str | None = None,
    marketplace: str = "amazon.in",
    max_listings: int = 10,
) -> dict[str, Any]:
    """Collect competitor image sets and find the gaps worth exploiting.

    Pass an ``asin`` for one product's full gallery (requires the scraper), or a
    ``product_name`` to survey the image coverage across a whole search page.
    """
    payload = ProductImageInput(
        product_name=product_name, asin=asin, marketplace=marketplace, max_listings=max_listings
    )
    if not payload.product_name and not payload.asin:
        raise InvalidInputError(
            "Provide either product_name or asin.",
            remediation="Pass product_name='silicone sink strainer' or asin='B0XXXXXXXX'.",
        )

    if payload.asin:
        scraped = await services.scraper.scrape_product(payload.asin)
        product = scraped["product"]
        images = product.get("image_urls") or []
        return {
            "mode": "single_product",
            "asin": product.get("asin"),
            "title": product.get("title"),
            "image_count": len(images),
            "image_urls": images,
            "assessment": _assess_single(len(images)),
            "gaps": _gaps(len(images)),
            "image_plan": _image_plan(product.get("title") or payload.asin),
            "requirements": AMAZON_IMAGE_REQUIREMENTS,
            **{key: scraped[key] for key in ("source", "data_type", "confidence", "last_updated")},
        }

    marketplace = services.amazon.validate_marketplace(payload.marketplace)
    listings = await services.amazon.search_products(payload.product_name, marketplace, payload.max_listings)
    counts = [item.image_count for item in listings if item.quality_known]
    thin = [
        {"title": item.title, "asin": item.asin, "image_count": item.image_count, "rating": item.rating}
        for item in listings
        if item.quality_known and item.image_count < 5
    ][:8]

    return {
        "mode": "keyword_survey",
        "product_name": payload.product_name,
        "marketplace": marketplace,
        "listings_analysed": len(listings),
        "listings_with_visible_gallery": len(counts),
        "average_image_count": round(sum(counts) / len(counts), 1) if counts else None,
        "listings_below_5_images": len(thin),
        "thin_galleries": thin,
        "share_with_video": (
            round(sum(1 for item in listings if item.quality_known and item.has_video) / len(counts), 2)
            if counts
            else None
        ),
        "share_with_aplus": (
            round(sum(1 for item in listings if item.quality_known and item.has_aplus_content) / len(counts), 2)
            if counts
            else None
        ),
        "note": (
            None
            if counts
            else "This data source does not expose galleries. Pass an asin, or enable the scraper, "
            "to inspect real image sets."
        ),
        "opportunity": _survey_opportunity(counts, thin),
        "image_plan": _image_plan(payload.product_name),
        "requirements": AMAZON_IMAGE_REQUIREMENTS,
        **services.amazon.provider.envelope("Image counts come from the configured provider.").as_dict(),
    }


AMAZON_IMAGE_REQUIREMENTS = [
    "Main image: pure white background (RGB 255,255,255), product filling about 85% of the frame.",
    "Minimum 1600 px on the longest side so hover-zoom is enabled; 1:1 square preferred.",
    "No text, logos, watermarks, borders or props in the main image.",
    "Up to 7 images plus one video; use every slot - unused slots are free real estate.",
    "JPEG (.jpg) is safest; sRGB colour profile.",
]


def _assess_single(count: int) -> str:
    if count == 0:
        return "No images parsed - the gallery may be JS-rendered. Retry with render=true on the scraper."
    if count >= RECOMMENDED_IMAGE_COUNT:
        return f"Strong: {count} images, using the full gallery."
    if count >= 5:
        return f"Adequate: {count} images, but {RECOMMENDED_IMAGE_COUNT - count} slots are still empty."
    return f"Weak: only {count} images. This is a direct opening for a better-presented listing."


def _gaps(count: int) -> list[str]:
    gaps = []
    if count < RECOMMENDED_IMAGE_COUNT:
        gaps.append(f"{RECOMMENDED_IMAGE_COUNT - count} unused image slots.")
    if count < 5:
        gaps.append("Too few images to answer buyer questions - expect avoidable returns and questions.")
    gaps.append("No video detected in the gallery (video is not exposed in the parsed image list).")
    return gaps


def _survey_opportunity(counts: list[int], thin: list[dict[str, Any]]) -> str:
    if not counts:
        return "Image coverage is not visible from this data source."
    average = sum(counts) / len(counts)
    if average < 5:
        return (
            f"Strong opportunity: competitors average only {average:.1f} images. A full 7-image gallery "
            "plus video will visibly outclass the page."
        )
    if thin:
        return f"Moderate opportunity: {len(thin)} listings run thin galleries you can beat directly."
    return (
        f"Competitors already average {average:.1f} images - match them, then differentiate on "
        "infographic quality and a comparison chart rather than count."
    )


def _image_plan(product_name: str | None) -> list[dict[str, str]]:
    """A concrete seven-slot gallery plan."""
    name = product_name or "the product"
    return [
        {"slot": "1 - Main", "brief": f"{name} on pure white, straight-on, filling 85% of frame."},
        {"slot": "2 - Scale", "brief": "In-hand or beside a common object so size is unmistakable."},
        {"slot": "3 - Lifestyle", "brief": f"{name} in use in a typical Indian home setting."},
        {"slot": "4 - Infographic", "brief": "Dimensions, weight and material callouts with icons."},
        {"slot": "5 - Problem/Solution", "brief": "Before and after showing the problem it removes."},
        {"slot": "6 - Comparison", "brief": "Versus ordinary alternatives: material, durability, warranty."},
        {"slot": "7 - What's in the box", "brief": "Flat lay of every component, plus packaging."},
    ]


def register(mcp: "MCPServerType", services: ServiceBundle) -> None:
    """Register ``analyze_product_images`` with the MCP server."""

    @mcp.tool(
        name="analyze_product_images",
        description=(
            "Analyse product imagery on Amazon India. Pass an ASIN to pull one listing's full image "
            "gallery, or a product_name to survey image coverage across a search page. Returns image "
            "counts, thin galleries you can beat, Amazon's image requirements and a concrete "
            "seven-slot gallery plan."
        ),
    )
    async def _analyze_product_images(
        product_name: str | None = None,
        asin: str | None = None,
        marketplace: str = "amazon.in",
        max_listings: int = 10,
    ) -> dict[str, Any]:
        return await analyze_product_images(services, product_name, asin, marketplace, max_listings)
