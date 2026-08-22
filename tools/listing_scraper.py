"""MCP tool: ``scrape_listing_details`` - a full competitor listing teardown."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from tools import ServiceBundle, tool_handler

if TYPE_CHECKING:  # pragma: no cover
    from tools import MCPServerType

logger = logging.getLogger(__name__)

# Amazon's own guidance, and what strong Indian listings actually do.
IDEAL_TITLE_MIN = 80
IDEAL_TITLE_MAX = 200
IDEAL_BULLETS = 5
IDEAL_IMAGES = 7
IDEAL_DESCRIPTION_CHARS = 1_000


class ListingDetailsInput(BaseModel):
    """Input schema for ``scrape_listing_details``."""

    asin: str = Field(min_length=10, max_length=10)
    render: bool = Field(default=False, description="Render with Chromium for JS-built sections")


@tool_handler
async def scrape_listing_details(services: ServiceBundle, asin: str, render: bool = False) -> dict[str, Any]:
    """Scrape one listing in full and grade it against Amazon best practice.

    Returns the raw listing content (title, images, bullets, description, A+,
    specifications, badges, variations) plus a teardown saying where the listing
    is strong, where it is weak, and what you would have to do to beat it.
    """
    payload = ListingDetailsInput(asin=asin, render=render)
    result = await services.scraper.scrape_listing_details(payload.asin, payload.render)
    listing = result["listing"]

    scorecard, score = _score_listing(listing)
    result["listing_scorecard"] = scorecard
    result["listing_quality_score"] = score
    result["strengths"] = [row["element"] for row in scorecard if row["verdict"] == "Strong"]
    result["weaknesses"] = [
        {"element": row["element"], "issue": row["detail"], "fix": row["fix"]}
        for row in scorecard
        if row["verdict"] in {"Weak", "Missing"}
    ]
    result["how_to_beat_this_listing"] = _how_to_beat(listing, scorecard, score)

    units = services.revenue.best_units_estimate(
        listing.get("bought_past_month"), listing.get("bsr"), listing.get("bsr_category") or "default"
    )
    if units and listing.get("price"):
        result["sales_estimate"] = {
            "estimated_monthly_units": units.units_per_month,
            "units_range": {"low": units.range_low, "high": units.range_high},
            "method": units.method,
            "estimated_monthly_revenue": round(units.units_per_month * listing["price"], 2),
            "confidence": units.confidence,
        }

    result["note"] = (
        "Fields that could not be parsed are returned as null or empty, never guessed. "
        "A long missing_fields list usually means Amazon changed its markup - retry with render=true."
    )
    return result


def _score_listing(listing: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """Grade each listing element, returning the scorecard and a 0-100 score."""
    title = listing.get("title") or ""
    title_length = listing.get("title_length") or 0
    bullets = listing.get("bullet_count") or 0
    images = listing.get("image_count") or 0
    description_length = listing.get("description_length") or 0
    rows: list[dict[str, Any]] = []

    rows.append(
        _row(
            "Title",
            weight=15,
            earned=15 if IDEAL_TITLE_MIN <= title_length <= IDEAL_TITLE_MAX else 6 if title_length else 0,
            detail=f"{title_length} characters",
            strong=IDEAL_TITLE_MIN <= title_length <= IDEAL_TITLE_MAX,
            fix=(
                f"Aim for {IDEAL_TITLE_MIN}-{IDEAL_TITLE_MAX} characters with the main keyword in the first 80."
                if not IDEAL_TITLE_MIN <= title_length <= IDEAL_TITLE_MAX
                else "Already well sized."
            ),
        )
    )
    rows.append(
        _row(
            "Bullet points",
            weight=20,
            earned=min(20, bullets * 4),
            detail=f"{bullets} of {IDEAL_BULLETS}",
            strong=bullets >= IDEAL_BULLETS,
            fix=f"Use all {IDEAL_BULLETS} bullets, each leading with a benefit in capitals.",
        )
    )
    rows.append(
        _row(
            "Images",
            weight=25,
            earned=min(25, round(images / IDEAL_IMAGES * 25)),
            detail=f"{images} of {IDEAL_IMAGES}",
            strong=images >= IDEAL_IMAGES,
            fix=f"Fill all {IDEAL_IMAGES} slots: main, scale, lifestyle, infographic, before/after, comparison, what's in the box.",
        )
    )
    rows.append(
        _row(
            "Product description",
            weight=10,
            earned=10 if description_length >= IDEAL_DESCRIPTION_CHARS else 5 if description_length > 200 else 0,
            detail=f"{description_length} characters",
            strong=description_length >= IDEAL_DESCRIPTION_CHARS,
            fix="Write at least 1,000 characters of problem/solution copy in short mobile-friendly paragraphs.",
        )
    )
    rows.append(
        _row(
            "A+ content",
            weight=15,
            earned=15 if listing.get("has_aplus_content") else 0,
            detail="present" if listing.get("has_aplus_content") else "absent",
            strong=bool(listing.get("has_aplus_content")),
            fix="Enrol in Brand Registry and add A+ content - it is free and lifts conversion.",
        )
    )
    rows.append(
        _row(
            "Video",
            weight=10,
            earned=10 if listing.get("has_video") else 0,
            detail="present" if listing.get("has_video") else "absent",
            strong=bool(listing.get("has_video")),
            fix="Add a 30-60 second demo video; most Indian listings still have none.",
        )
    )
    rows.append(
        _row(
            "Specifications",
            weight=5,
            earned=5 if len(listing.get("specifications") or {}) >= 5 else 2,
            detail=f"{len(listing.get('specifications') or {})} attributes",
            strong=len(listing.get("specifications") or {}) >= 5,
            fix="Fill every applicable attribute - they feed search filters buyers use.",
        )
    )

    score = min(100, sum(row["points_earned"] for row in rows))
    return rows, score


def _row(element: str, weight: int, earned: int, detail: str, strong: bool, fix: str) -> dict[str, Any]:
    verdict = "Strong" if strong else ("Missing" if earned == 0 else "Weak")
    return {
        "element": element,
        "verdict": verdict,
        "detail": detail,
        "points_earned": earned,
        "points_available": weight,
        "fix": fix,
    }


def _how_to_beat(listing: dict[str, Any], scorecard: list[dict[str, Any]], score: int) -> list[str]:
    """Concrete moves to outrank this specific listing."""
    gaps = [row for row in scorecard if row["verdict"] in {"Weak", "Missing"}]
    advice: list[str] = []

    if score < 55:
        advice.append(
            f"This listing scores {score}/100 - it is beatable on presentation alone. "
            "Match its price and out-present it."
        )
    elif score < 80:
        advice.append(f"A solid listing at {score}/100, with {len(gaps)} exploitable gaps.")
    else:
        advice.append(
            f"Strong listing at {score}/100. Presentation alone will not win - you need a better "
            "product, a bundle, or a sharper price."
        )

    for row in gaps[:3]:
        advice.append(f"They are weak on {row['element'].lower()} ({row['detail']}). {row['fix']}")

    rating = listing.get("rating")
    if rating and rating < 4.0:
        advice.append(
            f"Their rating is {rating} - run analyze_reviews on this ASIN to find the complaint they "
            "have not fixed, then lead your listing on solving it."
        )
    if not listing.get("has_coupon"):
        advice.append("No coupon badge on their listing - a launch coupon would give you a visible edge in search.")
    if (listing.get("variation_count") or 0) <= 1:
        advice.append("Single variation only - offering size or colour options captures demand they cannot serve.")
    return advice


def register(mcp: "MCPServerType", services: ServiceBundle) -> None:
    """Register ``scrape_listing_details`` with the MCP server."""

    @mcp.tool(
        name="scrape_listing_details",
        description=(
            "Scrape a complete Amazon India listing by ASIN: title, all images, bullet points, "
            "description, A+ content, video, specifications table, category path, variations, badges, "
            "coupon, seller, delivery, BSR and price/discount. Then grades the listing 0-100 against "
            "Amazon best practice and tells you exactly how to beat it."
        ),
    )
    async def _scrape_listing_details(asin: str, render: bool = False) -> dict[str, Any]:
        return await scrape_listing_details(services, asin, render)
