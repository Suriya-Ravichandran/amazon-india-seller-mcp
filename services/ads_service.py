"""Amazon Ads (Sponsored Products) logic: keywords, bids and campaign structure.

The whole of PPC comes down to one equation:

    ACOS = ad spend / ad sales
    CPC  = selling price x conversion rate x ACOS

So the most you can pay per click is set by your price, how often clicks convert,
and how much of each sale you are willing to give back. Break-even ACOS is simply
your profit margin - spend more than that and every order loses money.

Conversion rates and CPC benchmarks here are **assumptions**, labelled
``Estimated``. Replace them with your own search-term report as soon as you have
one; nothing in this module pretends to be measured campaign data.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from config.settings import Settings, get_settings
from services import Confidence, DataEnvelope, DataType, InvalidInputError

logger = logging.getLogger(__name__)

MatchType = Literal["exact", "phrase", "broad", "auto"]

# Bid multipliers relative to the exact-match bid. Tighter targeting earns a
# higher bid because its traffic is more qualified.
MATCH_TYPE_MULTIPLIERS: dict[str, float] = {
    "exact": 1.00,
    "phrase": 0.85,
    "broad": 0.70,
    "auto": 0.65,
}

# Category conversion-rate assumptions for Amazon India, as fractions.
CATEGORY_CONVERSION_RATES: dict[str, float] = {
    "Home & Kitchen": 0.10,
    "Kitchen": 0.10,
    "Mobile Accessories": 0.08,
    "Electronics Accessories": 0.07,
    "Office Products": 0.09,
    "Beauty": 0.08,
    "Health & Personal Care": 0.09,
    "Baby": 0.10,
    "Sports & Fitness": 0.07,
    "Pet Supplies": 0.09,
    "Toys": 0.08,
    "Automotive Accessories": 0.06,
    "Apparel": 0.05,
    "Grocery": 0.11,
    "default": 0.08,
}

# Indicative Sponsored Products CPC bands (INR) for Amazon India.
CATEGORY_CPC_BANDS: dict[str, tuple[float, float]] = {
    "Home & Kitchen": (4.0, 14.0),
    "Mobile Accessories": (5.0, 18.0),
    "Electronics Accessories": (6.0, 22.0),
    "Office Products": (3.5, 12.0),
    "Beauty": (6.0, 20.0),
    "Health & Personal Care": (5.0, 18.0),
    "Baby": (5.0, 16.0),
    "Sports & Fitness": (4.0, 15.0),
    "Pet Supplies": (4.0, 13.0),
    "Toys": (4.0, 14.0),
    "Apparel": (5.0, 17.0),
    "Grocery": (3.0, 10.0),
    "default": (4.0, 15.0),
}

# Terms that waste spend on almost every Indian listing.
UNIVERSAL_NEGATIVES: tuple[str, ...] = (
    "free", "cheap", "second hand", "used", "repair", "manual pdf", "wholesale",
    "job", "salary", "images", "meaning", "how to make", "diy", "recipe",
)


class KeywordBid(BaseModel):
    """One ad keyword with its recommended match type and bid."""

    keyword: str
    match_type: str
    suggested_bid: float
    bid_range: dict[str, float]
    estimated_clicks_per_order: int
    estimated_cost_per_order: float
    priority: str
    rationale: str
    campaign: str


class BidMath(BaseModel):
    """The core bid economics for one product."""

    selling_price: float
    profit_per_unit: float
    profit_margin: float
    break_even_acos: float
    target_acos: float
    conversion_rate: float
    break_even_cpc: float
    max_profitable_cpc: float
    target_cpc: float
    clicks_per_order: int
    ad_cost_per_order: float
    profit_after_ads: float
    roas_at_target: float


class AdsService:
    """Sponsored Products bid maths, keyword grouping and campaign structure."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    # -- assumptions ------------------------------------------------------ #
    @staticmethod
    def conversion_rate_for(category: str) -> float:
        for name, rate in CATEGORY_CONVERSION_RATES.items():
            if name.lower() == (category or "").strip().lower():
                return rate
        return CATEGORY_CONVERSION_RATES["default"]

    @staticmethod
    def cpc_band_for(category: str) -> tuple[float, float]:
        for name, band in CATEGORY_CPC_BANDS.items():
            if name.lower() == (category or "").strip().lower():
                return band
        return CATEGORY_CPC_BANDS["default"]

    # -- core maths ------------------------------------------------------- #
    def bid_math(
        self,
        selling_price: float,
        profit_per_unit: float,
        category: str = "Home & Kitchen",
        conversion_rate: float | None = None,
        target_acos: float | None = None,
    ) -> BidMath:
        """Work out break-even and target CPC from unit economics.

        ``target_acos`` defaults to 60% of break-even, which keeps roughly 40% of
        the unit profit while still buying volume.
        """
        if selling_price <= 0:
            raise InvalidInputError("Invalid selling price: it must be greater than 0.")
        if profit_per_unit <= 0:
            raise InvalidInputError(
                "Profit per unit must be positive before you advertise.",
                remediation="Fix the unit economics first with calculate_profitability; "
                "advertising a loss-making product only loses money faster.",
            )

        conversion_rate = conversion_rate if conversion_rate is not None else self.conversion_rate_for(category)
        if not 0 < conversion_rate <= 1:
            raise InvalidInputError("conversion_rate must be a fraction between 0 and 1 (e.g. 0.10 for 10%).")

        margin = profit_per_unit / selling_price
        break_even_acos = margin                      # spend the whole margin and profit is zero
        target_acos = target_acos if target_acos is not None else break_even_acos * 0.6
        if not 0 < target_acos <= 2:
            raise InvalidInputError("target_acos must be a fraction between 0 and 2 (e.g. 0.25 for 25%).")

        # CPC = price x conversion rate x ACOS
        break_even_cpc = selling_price * conversion_rate * break_even_acos
        target_cpc = selling_price * conversion_rate * target_acos
        clicks_per_order = max(1, round(1 / conversion_rate))
        ad_cost_per_order = round(target_cpc * clicks_per_order, 2)

        return BidMath(
            selling_price=round(selling_price, 2),
            profit_per_unit=round(profit_per_unit, 2),
            profit_margin=round(margin, 4),
            break_even_acos=round(break_even_acos, 4),
            target_acos=round(target_acos, 4),
            conversion_rate=round(conversion_rate, 4),
            break_even_cpc=round(break_even_cpc, 2),
            max_profitable_cpc=round(break_even_cpc, 2),
            target_cpc=round(target_cpc, 2),
            clicks_per_order=clicks_per_order,
            ad_cost_per_order=ad_cost_per_order,
            profit_after_ads=round(profit_per_unit - ad_cost_per_order, 2),
            roas_at_target=round(1 / target_acos, 2) if target_acos else 0.0,
        )

    # -- keyword bids ----------------------------------------------------- #
    def build_keyword_bids(
        self,
        keywords: list[dict[str, Any]],
        math: BidMath,
        category: str = "Home & Kitchen",
    ) -> list[KeywordBid]:
        """Turn researched keywords into ad keywords with match types and bids.

        Each keyword is placed in the campaign that suits its intent: tight
        exact-match for proven head terms, phrase for mid-tail, broad for
        discovery.
        """
        floor, ceiling = self.cpc_band_for(category)
        rows: list[KeywordBid] = []

        for entry in keywords:
            keyword = str(entry.get("keyword") or "").strip().lower()
            if len(keyword) < 3:
                continue
            group = str(entry.get("group") or "Secondary")
            competition = float(entry.get("competition_index") or 0.5)
            word_count = len(keyword.split())

            match_type, campaign = _placement_for(group, word_count)
            multiplier = MATCH_TYPE_MULTIPLIERS[match_type]

            # Competitive terms cost more to win; long-tail terms cost less.
            bid = math.target_cpc * multiplier * (0.8 + competition * 0.6)
            bid = max(1.0, min(bid, math.break_even_cpc * 1.1, ceiling))

            rows.append(
                KeywordBid(
                    keyword=keyword,
                    match_type=match_type,
                    suggested_bid=round(bid, 2),
                    bid_range={"low": round(max(1.0, bid * 0.7), 2), "high": round(min(bid * 1.4, ceiling), 2)},
                    estimated_clicks_per_order=math.clicks_per_order,
                    estimated_cost_per_order=round(bid * math.clicks_per_order, 2),
                    priority=_priority(group, competition),
                    rationale=_rationale(group, match_type, competition, bid, math),
                    campaign=campaign,
                )
            )

        rows.sort(key=lambda row: (-MATCH_TYPE_MULTIPLIERS[row.match_type], -row.suggested_bid))
        return rows

    # -- campaign structure ----------------------------------------------- #
    def campaign_structure(
        self, product_name: str, math: BidMath, daily_budget: float, keyword_rows: list[KeywordBid]
    ) -> list[dict[str, Any]]:
        """A three-campaign launch structure with the budget split across it."""
        by_campaign: dict[str, list[KeywordBid]] = {}
        for row in keyword_rows:
            by_campaign.setdefault(row.campaign, []).append(row)

        # Auto discovers terms, exact harvests the winners, phrase/broad fills the middle.
        splits = {
            "Auto - Discovery": 0.25,
            "Manual Exact - Core": 0.45,
            "Manual Phrase/Broad - Expansion": 0.30,
        }
        structure = []
        for name, share in splits.items():
            budget = round(daily_budget * share, 2)
            rows = by_campaign.get(name, [])
            structure.append(
                {
                    "campaign": name,
                    "daily_budget": budget,
                    "monthly_budget": round(budget * 30, 2),
                    "targeting": _targeting_note(name),
                    "default_bid": round(
                        math.target_cpc * MATCH_TYPE_MULTIPLIERS["auto" if "Auto" in name else "exact"], 2
                    ),
                    "keyword_count": len(rows),
                    "keywords": [row.keyword for row in rows[:20]],
                    "estimated_clicks_per_day": int(budget / math.target_cpc) if math.target_cpc else 0,
                    "estimated_orders_per_day": round(
                        (budget / math.target_cpc) * math.conversion_rate, 1
                    )
                    if math.target_cpc
                    else 0.0,
                    "purpose": _campaign_purpose(name),
                }
            )
        return structure

    def negative_keywords(self, product_name: str, keywords: list[str]) -> dict[str, list[str]]:
        """Negatives to add on day one, before they waste spend."""
        tokens = {token for keyword in keywords for token in keyword.lower().split()}
        brand_negatives = [token for token in tokens if len(token) > 3 and token.istitle()]
        return {
            "add_immediately": list(UNIVERSAL_NEGATIVES),
            "review_after_two_weeks": [
                "Any search term with clicks but no orders after 10+ clicks.",
                "Terms describing a different product size, material or use case.",
                "Competitor brand names, unless you are deliberately conquesting.",
            ],
            "brand_terms_detected": brand_negatives[:5],
            "note": (
                "Negatives are how PPC stops leaking money. Check the search-term report weekly "
                "and negate anything spending more than one order's profit without converting."
            ),
        }

    def envelope(self) -> DataEnvelope:
        return DataEnvelope(
            source="Amazon Ads bid model (category benchmarks)",
            data_type=DataType.ESTIMATED,
            confidence=Confidence.LOW,
            notes=(
                "Conversion rates and CPC bands are category assumptions, not your campaign data. "
                "Replace them with your own search-term report once you have 2-4 weeks of spend."
            ),
        )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _placement_for(group: str, word_count: int) -> tuple[str, str]:
    """Decide match type and campaign from keyword group and length."""
    if group == "Primary" or word_count <= 2:
        return "exact", "Manual Exact - Core"
    if group == "Long-Tail" or word_count >= 4:
        return "phrase", "Manual Phrase/Broad - Expansion"
    return "broad", "Manual Phrase/Broad - Expansion"


def _priority(group: str, competition: float) -> str:
    if group == "Primary" and competition < 0.8:
        return "High"
    if group == "Long-Tail" and competition < 0.4:
        return "High"  # cheap, well-qualified traffic
    if competition > 0.85:
        return "Low"
    return "Medium"


def _rationale(group: str, match_type: str, competition: float, bid: float, math: BidMath) -> str:
    parts = [f"{group} keyword on {match_type} match"]
    if competition > 0.8:
        parts.append("highly competitive, so expect a low impression share at this bid")
    elif competition < 0.35:
        parts.append("low competition, usually the cheapest qualified traffic available")
    if bid >= math.break_even_cpc:
        parts.append(f"at the break-even ceiling of Rs.{math.break_even_cpc:.2f} - watch it closely")
    else:
        headroom = (math.break_even_cpc - bid) / math.break_even_cpc * 100
        parts.append(f"{headroom:.0f}% below the break-even CPC")
    return "; ".join(parts) + "."


def _targeting_note(campaign: str) -> str:
    return {
        "Auto - Discovery": "Automatic targeting, all four groups on. Harvest search terms weekly.",
        "Manual Exact - Core": "Exact match only. Move proven converters here from Auto.",
        "Manual Phrase/Broad - Expansion": "Phrase and broad match for mid and long-tail discovery.",
    }.get(campaign, "Manual targeting.")


def _campaign_purpose(campaign: str) -> str:
    return {
        "Auto - Discovery": "Finds the search terms real buyers use - your keyword research for free.",
        "Manual Exact - Core": "Where profit is made. Tight control, highest bids, best converters only.",
        "Manual Phrase/Broad - Expansion": "Widens reach once the core is stable. Kill terms that do not convert.",
    }.get(campaign, "")
