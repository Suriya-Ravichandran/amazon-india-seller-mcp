"""MCP tool: ``suggest_ppc_keywords`` - ad keywords with match types and bids."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from amazon_india_seller_mcp.tools import ServiceBundle, tool_handler

if TYPE_CHECKING:  # pragma: no cover
    from amazon_india_seller_mcp.tools import MCPServerType

logger = logging.getLogger(__name__)


class PPCKeywordInput(BaseModel):
    """Input schema for ``suggest_ppc_keywords``."""

    product_name: str = Field(min_length=2, max_length=200)
    selling_price: float = Field(gt=0, le=500_000)
    profit_per_unit: float | None = Field(default=None)
    product_cost: float | None = Field(default=None, ge=0)
    category: str = Field(default="Home & Kitchen")
    target_acos: float | None = Field(default=None, gt=0, le=2)
    conversion_rate: float | None = Field(default=None, gt=0, le=1)
    include_competitor_terms: bool = Field(default=True)


@tool_handler
async def suggest_ppc_keywords(
    services: ServiceBundle,
    product_name: str,
    selling_price: float,
    profit_per_unit: float | None = None,
    product_cost: float | None = None,
    category: str = "Home & Kitchen",
    target_acos: float | None = None,
    conversion_rate: float | None = None,
    include_competitor_terms: bool = True,
) -> dict[str, Any]:
    """Suggest Sponsored Products keywords with match type, bid and priority.

    Supply either ``profit_per_unit`` or ``product_cost`` — the bid ceiling comes
    from your unit profit, so without one of them there is no way to say what a
    click is worth.
    """
    payload = PPCKeywordInput(
        product_name=product_name,
        selling_price=selling_price,
        profit_per_unit=profit_per_unit,
        product_cost=product_cost,
        category=category,
        target_acos=target_acos,
        conversion_rate=conversion_rate,
        include_competitor_terms=include_competitor_terms,
    )

    profit = payload.profit_per_unit
    unit_economics: dict[str, Any] | None = None
    if profit is None:
        if payload.product_cost is None:
            from amazon_india_seller_mcp.services import InvalidInputError

            raise InvalidInputError(
                "Provide either profit_per_unit or product_cost.",
                remediation="Pass product_cost=120, or run calculate_profitability first and pass its profit.",
            )
        breakdown = services.pricing.calculate(
            selling_price=payload.selling_price,
            product_cost=payload.product_cost,
            category=payload.category,
        )
        profit = breakdown.estimated_profit
        unit_economics = {
            "profit_per_unit": breakdown.estimated_profit,
            "profit_margin_percent": round(breakdown.profit_margin * 100, 2),
            "total_cost_per_unit": breakdown.total_cost,
            "source": "calculate_profitability with the supplied product cost",
        }

    math = services.ads.bid_math(
        selling_price=payload.selling_price,
        profit_per_unit=profit,
        category=payload.category,
        conversion_rate=payload.conversion_rate,
        target_acos=payload.target_acos,
    )

    # Seed from keyword research, then widen with real competitor title terms.
    research = await services.trends.research_keywords(payload.product_name)
    seeds: list[dict[str, Any]] = list(research["keyword_table"])
    competitor_terms: list[str] = []
    if payload.include_competitor_terms:
        competitor_terms = await _competitor_terms(services, payload.product_name)
        seeds += [
            {"keyword": term, "group": "Secondary", "competition_index": 0.55} for term in competitor_terms
        ]

    rows = services.ads.build_keyword_bids(seeds, math, payload.category)
    negatives = services.ads.negative_keywords(payload.product_name, [row.keyword for row in rows])

    by_match: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_match.setdefault(row.match_type, []).append(row.model_dump())

    return {
        "product_name": payload.product_name,
        "category": payload.category,
        "bid_economics": math.model_dump(),
        "unit_economics": unit_economics,
        "keyword_count": len(rows),
        "keywords": [row.model_dump() for row in rows[:40]],
        "keywords_by_match_type": {
            match: {"count": len(items), "keywords": [row["keyword"] for row in items[:15]]}
            for match, items in by_match.items()
        },
        "high_priority_keywords": [row.keyword for row in rows if row.priority == "High"][:12],
        "competitor_terms_harvested": competitor_terms[:15],
        "negative_keywords": negatives,
        "bidding_rules": [
            f"Never bid above Rs.{math.break_even_cpc:.2f} - that is the break-even CPC where profit hits zero.",
            f"Start at the suggested bid, then raise it only on keywords converting under {math.target_acos * 100:.0f}% ACOS.",
            f"Expect roughly {math.clicks_per_order} clicks per order at a {math.conversion_rate * 100:.0f}% conversion rate.",
            "Pause any keyword with 15+ clicks and no order; the data is telling you it does not convert.",
        ],
        "caveat": (
            "Conversion rate and CPC bands are category assumptions, not your campaign data. Recalculate "
            "with real numbers once you have 2-4 weeks of search-term reports."
        ),
        **services.ads.envelope().as_dict(),
    }


async def _competitor_terms(services: ServiceBundle, product_name: str) -> list[str]:
    """Mine competitor titles for terms real listings rank on."""
    try:
        listings = await services.amazon.search_products(product_name, "amazon.in", limit=15)
    except Exception:  # noqa: BLE001 - keyword suggestions must survive a data outage
        logger.info("Could not harvest competitor terms for %s", product_name)
        return []

    stopwords = {
        "the", "and", "for", "with", "from", "pack", "set", "pcs", "piece", "free",
        "new", "best", "premium", "quality", "your", "you", "use", "used", "size",
    }
    counts: dict[str, int] = {}
    for listing in listings:
        words = [word.strip(",.|()-").lower() for word in (listing.title or "").split()]
        words = [word for word in words if len(word) > 3 and word not in stopwords and word.isalpha()]
        for first, second in zip(words, words[1:]):
            phrase = f"{first} {second}"
            counts[phrase] = counts.get(phrase, 0) + 1

    return [phrase for phrase, count in sorted(counts.items(), key=lambda item: -item[1]) if count >= 2][:20]


def register(mcp: "MCPServerType", services: ServiceBundle) -> None:
    """Register ``suggest_ppc_keywords`` with the MCP server."""

    @mcp.tool(
        name="suggest_ppc_keywords",
        description=(
            "Suggest Amazon Ads (Sponsored Products) keywords for a product: each with a recommended "
            "match type (exact / phrase / broad), a suggested bid and bid range derived from your unit "
            "profit, priority, and which campaign it belongs in. Harvests terms from competitor titles, "
            "and returns negative keywords plus the break-even CPC you must never bid past."
        ),
    )
    async def _suggest_ppc_keywords(
        product_name: str,
        selling_price: float,
        profit_per_unit: float | None = None,
        product_cost: float | None = None,
        category: str = "Home & Kitchen",
        target_acos: float | None = None,
        conversion_rate: float | None = None,
        include_competitor_terms: bool = True,
    ) -> dict[str, Any]:
        return await suggest_ppc_keywords(
            services, product_name, selling_price, profit_per_unit, product_cost,
            category, target_acos, conversion_rate, include_competitor_terms,
        )
