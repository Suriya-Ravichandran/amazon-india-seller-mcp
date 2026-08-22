"""Revenue estimation, competitor classification and evergreen scoring.

Three questions this service answers:

1. **How many units does this sell, and what is that worth?** Preferring Amazon's
   own "bought in past month" badge when present, falling back to a BSR-to-units
   curve when it is not.
2. **Who am I actually competing with?** Which competitors are new sellers
   (proof a newcomer can rank) and which clear a real sales bar.
3. **Is this evergreen?** Stable, non-seasonal, year-round demand rather than a
   spike.

Unit estimates from BSR are modelled, never measured. Every result says which
method produced it and how much to trust it.
"""

from __future__ import annotations

import logging
import math
import statistics
from typing import Any

from pydantic import BaseModel, Field

from config.settings import Settings, get_settings
from services import (
    Confidence,
    DataEnvelope,
    DataType,
    InvalidInputError,
    utc_now_iso,
)

logger = logging.getLogger(__name__)

# Power-law coefficients for Amazon India: units_per_month ~= a * rank^-b.
# Approximations for a mid-size marketplace, deliberately conservative. Replace
# them once you can calibrate against your own sales data.
BSR_CURVES: dict[str, tuple[float, float]] = {
    "Home & Kitchen": (9_000, 0.55),
    "Kitchen": (9_000, 0.55),
    "Beauty": (7_000, 0.56),
    "Health & Personal Care": (7_500, 0.55),
    "Sports & Fitness": (4_500, 0.58),
    "Office Products": (4_000, 0.58),
    "Stationery": (4_000, 0.58),
    "Mobile Accessories": (11_000, 0.54),
    "Electronics Accessories": (9_500, 0.55),
    "Toys": (5_000, 0.57),
    "Baby": (5_500, 0.56),
    "Pet Supplies": (3_500, 0.59),
    "Automotive Accessories": (3_800, 0.58),
    "Grocery": (8_000, 0.56),
    "Apparel": (6_000, 0.60),
    "default": (6_000, 0.57),
}


class UnitsEstimate(BaseModel):
    """Monthly unit sales for one listing, with its provenance."""

    units_per_month: int
    method: str
    confidence: str
    basis: str
    range_low: int
    range_high: int


class CompetitorProfile(BaseModel):
    """One competitor, classified for a new seller's purposes."""

    title: str | None = None
    asin: str | None = None
    brand: str | None = None
    price: float | None = None
    rating: float | None = None
    review_count: int | None = None
    bought_past_month: int | None = None
    bsr: int | None = None
    estimated_monthly_units: int = 0
    estimated_monthly_revenue: float = 0.0
    units_method: str = "unknown"
    is_new_seller: bool = False
    meets_volume_target: bool = False
    seller_stage: str = "Unknown"
    market_share_percent: float = 0.0
    notes: list[str] = Field(default_factory=list)


class RevenueService:
    """Units, revenue, competitor stage and evergreen stability."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    # -- units ------------------------------------------------------------ #
    def units_from_bsr(self, bsr: int | None, category: str = "default") -> UnitsEstimate | None:
        """Model monthly units from a best-seller rank."""
        if not bsr or bsr <= 0:
            return None
        coefficient, exponent = BSR_CURVES.get(category, BSR_CURVES["default"])
        units = coefficient * math.pow(bsr, -exponent)
        units = max(1, int(round(units)))
        # The curve is a rough fit, so the honest output is a wide band.
        return UnitsEstimate(
            units_per_month=units,
            method="bsr_curve",
            confidence=Confidence.LOW.value,
            basis=f"BSR #{bsr:,} in {category} via {coefficient:.0f} x rank^-{exponent}",
            range_low=max(1, int(units * 0.4)),
            range_high=int(units * 2.5),
        )

    def units_from_bought_badge(self, bought_past_month: int | None) -> UnitsEstimate | None:
        """Use Amazon's own '500+ bought in past month' badge.

        This is Amazon's published figure, so it beats any model - but it is a
        floor ("500+"), not an exact count.
        """
        if not bought_past_month or bought_past_month <= 0:
            return None
        return UnitsEstimate(
            units_per_month=bought_past_month,
            method="bought_in_past_month_badge",
            confidence=Confidence.HIGH.value,
            basis=f"Amazon's own '{bought_past_month:,}+ bought in past month' badge",
            range_low=bought_past_month,
            range_high=int(bought_past_month * 2),
        )

    def best_units_estimate(
        self, bought_past_month: int | None, bsr: int | None, category: str = "default"
    ) -> UnitsEstimate | None:
        """Prefer Amazon's published badge; fall back to the BSR curve."""
        return self.units_from_bought_badge(bought_past_month) or self.units_from_bsr(bsr, category)

    # -- revenue ---------------------------------------------------------- #
    def calculate_revenue(
        self,
        price: float,
        units_per_month: int | None = None,
        bsr: int | None = None,
        bought_past_month: int | None = None,
        category: str = "default",
        net_profit_per_unit: float | None = None,
    ) -> dict[str, Any]:
        """Gross and (optionally) net monthly and annual revenue for one listing."""
        if price <= 0:
            raise InvalidInputError("Invalid price: it must be greater than 0.")

        estimate = None
        if units_per_month is not None:
            if units_per_month < 0:
                raise InvalidInputError("Invalid units_per_month: it cannot be negative.")
            estimate = UnitsEstimate(
                units_per_month=units_per_month,
                method="user_supplied",
                confidence=Confidence.HIGH.value,
                basis="Units supplied directly by the caller",
                range_low=units_per_month,
                range_high=units_per_month,
            )
        else:
            estimate = self.best_units_estimate(bought_past_month, bsr, category)

        if estimate is None:
            raise InvalidInputError(
                "Cannot estimate revenue without units, a BSR or a 'bought in past month' figure.",
                remediation="Pass units_per_month, bsr, or bought_past_month.",
            )

        gross_monthly = round(price * estimate.units_per_month, 2)
        result = {
            "price": round(price, 2),
            "estimated_monthly_units": estimate.units_per_month,
            "units_range": {"low": estimate.range_low, "high": estimate.range_high},
            "units_method": estimate.method,
            "units_basis": estimate.basis,
            "gross_monthly_revenue": gross_monthly,
            "gross_annual_revenue": round(gross_monthly * 12, 2),
            "revenue_range_monthly": {
                "low": round(price * estimate.range_low, 2),
                "high": round(price * estimate.range_high, 2),
            },
            "currency": "INR",
            "confidence": estimate.confidence,
        }
        if net_profit_per_unit is not None:
            result["net_profit_per_unit"] = round(net_profit_per_unit, 2)
            result["estimated_monthly_profit"] = round(net_profit_per_unit * estimate.units_per_month, 2)
            result["estimated_annual_profit"] = round(net_profit_per_unit * estimate.units_per_month * 12, 2)
        return result

    # -- competitors ------------------------------------------------------ #
    def profile_competitor(self, row: dict[str, Any], category: str = "default") -> CompetitorProfile:
        """Classify one competitor: sales, seller stage and whether it clears the volume bar."""
        price = _as_float(row.get("price"))
        reviews = _as_int(row.get("review_count"))
        bought = _as_int(row.get("bought_past_month"))
        bsr = _as_int(row.get("bsr"))
        estimate = self.best_units_estimate(bought, bsr, category)

        units = estimate.units_per_month if estimate else 0
        revenue = round((price or 0.0) * units, 2)
        review_threshold = self.settings.new_seller_review_threshold
        is_new = reviews is not None and reviews <= review_threshold
        meets_target = units >= self.settings.min_monthly_units_target

        notes: list[str] = []
        if is_new:
            notes.append(
                f"Only {reviews} reviews (at or below the {review_threshold} threshold) - "
                "reads as a new or recently launched seller."
            )
        if is_new and meets_target:
            notes.append(
                "A newcomer is already clearing the volume bar here: strong evidence a new seller can rank."
            )
        if not meets_target and units:
            notes.append(
                f"About {units:,} units/month is below the {self.settings.min_monthly_units_target} target."
            )
        if estimate and estimate.method == "bsr_curve":
            notes.append("Units modelled from BSR - treat as a wide band, not a measurement.")

        return CompetitorProfile(
            title=row.get("title"),
            asin=row.get("asin"),
            brand=row.get("brand"),
            price=price,
            rating=_as_float(row.get("rating")),
            review_count=reviews,
            bought_past_month=bought,
            bsr=bsr,
            estimated_monthly_units=units,
            estimated_monthly_revenue=revenue,
            units_method=estimate.method if estimate else "unavailable",
            is_new_seller=is_new,
            meets_volume_target=meets_target,
            seller_stage=_seller_stage(reviews, review_threshold),
            notes=notes,
        )

    def analyze_competitor_field(
        self, rows: list[dict[str, Any]], category: str = "default"
    ) -> dict[str, Any]:
        """Analyse a set of competitors: market size, new-seller share, concentration."""
        if not rows:
            raise InvalidInputError("No competitor rows supplied.")

        profiles = [self.profile_competitor(row, category) for row in rows]
        total_revenue = sum(profile.estimated_monthly_revenue for profile in profiles)
        total_units = sum(profile.estimated_monthly_units for profile in profiles)
        for profile in profiles:
            profile.market_share_percent = (
                round(profile.estimated_monthly_revenue / total_revenue * 100, 2) if total_revenue else 0.0
            )

        new_sellers = [p for p in profiles if p.is_new_seller]
        performers = [p for p in profiles if p.meets_volume_target]
        new_and_performing = [p for p in profiles if p.is_new_seller and p.meets_volume_target]
        prices = [p.price for p in profiles if p.price]
        hhi = sum((p.market_share_percent / 100) ** 2 for p in profiles)

        return {
            "competitors_analysed": len(profiles),
            "estimated_market_size_monthly_units": total_units,
            "estimated_market_size_monthly_revenue": round(total_revenue, 2),
            "estimated_market_size_annual_revenue": round(total_revenue * 12, 2),
            "average_monthly_units_per_listing": int(total_units / len(profiles)) if profiles else 0,
            "new_sellers": {
                "count": len(new_sellers),
                "share_percent": round(len(new_sellers) / len(profiles) * 100, 1),
                "review_threshold_used": self.settings.new_seller_review_threshold,
                "succeeding_count": len(new_and_performing),
                "verdict": _new_seller_verdict(len(new_sellers), len(new_and_performing), len(profiles)),
            },
            "volume_target": {
                "target_units_per_month": self.settings.min_monthly_units_target,
                "listings_meeting_target": len(performers),
                "share_percent": round(len(performers) / len(profiles) * 100, 1),
                "verdict": _volume_verdict(len(performers), len(profiles), self.settings.min_monthly_units_target),
            },
            "market_concentration": {
                "hhi": round(hhi, 3),
                "level": "High" if hhi > 0.25 else "Moderate" if hhi > 0.15 else "Low",
                "top_3_revenue_share_percent": round(
                    sum(
                        p.market_share_percent
                        for p in sorted(profiles, key=lambda x: -x.estimated_monthly_revenue)[:3]
                    ),
                    1,
                ),
            },
            "price_band": {
                "min": min(prices) if prices else None,
                "max": max(prices) if prices else None,
                "median": round(statistics.median(prices), 2) if prices else None,
            },
            "competitors": [profile.model_dump() for profile in profiles],
            "entry_verdict": _entry_verdict(profiles, new_and_performing, performers),
            **DataEnvelope(
                source="Revenue and competitor model",
                data_type=DataType.ESTIMATED,
                confidence=Confidence.LOW,
                notes=(
                    "Unit and revenue figures are modelled from BSR unless a 'bought in past month' "
                    "badge was available. Market size covers only the sampled listings, not the whole category."
                ),
            ).as_dict(),
        }

    # -- evergreen -------------------------------------------------------- #
    def evergreen_analysis(
        self,
        product_name: str,
        monthly_interest: list[float],
        trend_direction: str = "Stable",
        source: str = "Internal model",
        data_type: DataType = DataType.ESTIMATED,
    ) -> dict[str, Any]:
        """Score how evergreen a product is from its interest-over-time series.

        ``monthly_interest`` is a series of relative search-interest values,
        oldest first - typically 12 to 60 monthly points from Google Trends.
        """
        series = [float(value) for value in monthly_interest if value is not None]
        if len(series) < 6:
            raise InvalidInputError(
                "Need at least 6 interest data points to judge whether demand is evergreen.",
                remediation="Enable Google Trends (GOOGLE_TRENDS_ENABLED=true) for a real 5-year series.",
            )

        mean = statistics.fmean(series)
        stdev = statistics.pstdev(series)
        cv = (stdev / mean) if mean else 1.0             # lower = steadier
        peak_ratio = (max(series) / mean) if mean else 1.0  # closer to 1 = flatter
        trough_ratio = (min(series) / mean) if mean else 0.0

        half = len(series) // 2
        first_half = statistics.fmean(series[:half]) or 1.0
        second_half = statistics.fmean(series[half:])
        growth = (second_half - first_half) / first_half

        stability_score = max(0.0, min(100.0, 100 - cv * 160))
        flatness_score = max(0.0, min(100.0, 100 - (peak_ratio - 1) * 110))
        floor_score = max(0.0, min(100.0, trough_ratio * 130))
        growth_score = max(0.0, min(100.0, 60 + growth * 130))
        evergreen_score = round(
            stability_score * 0.35 + flatness_score * 0.25 + floor_score * 0.25 + growth_score * 0.15, 1
        )

        if evergreen_score >= 78:
            verdict, guidance = "Evergreen", "Steady year-round demand - safe to hold stock continuously."
        elif evergreen_score >= 62:
            verdict, guidance = "Mostly Evergreen", "Largely stable with mild peaks; plan light seasonal stock."
        elif evergreen_score >= 45:
            verdict, guidance = "Semi-Seasonal", "Noticeable peaks - time your inventory to them."
        elif evergreen_score >= 30:
            verdict, guidance = "Seasonal", "Demand concentrates in a few months; risky as a first product."
        else:
            verdict, guidance = "Highly Seasonal / Fad", "Avoid as a beginner product - you can be left with dead stock."

        return {
            "product_name": product_name,
            "evergreen_score": evergreen_score,
            "verdict": verdict,
            "guidance": guidance,
            "is_evergreen": evergreen_score >= 62,
            "data_points": len(series),
            "components": {
                "stability": round(stability_score, 1),
                "flatness": round(flatness_score, 1),
                "demand_floor": round(floor_score, 1),
                "growth": round(growth_score, 1),
            },
            "metrics": {
                "mean_interest": round(mean, 1),
                "coefficient_of_variation": round(cv, 3),
                "peak_to_mean_ratio": round(peak_ratio, 2),
                "trough_to_mean_ratio": round(trough_ratio, 2),
                "half_over_half_growth_percent": round(growth * 100, 1),
            },
            "trend_direction": trend_direction,
            "interpretation": [
                f"Interest varies {cv * 100:.0f}% around its own average (lower is steadier).",
                f"The busiest period runs {peak_ratio:.1f}x the average.",
                f"The quietest period still holds {trough_ratio * 100:.0f}% of average demand.",
                f"Recent half vs earlier half: {growth * 100:+.0f}%.",
            ],
            **DataEnvelope(
                source=source,
                data_type=data_type,
                confidence=Confidence.MEDIUM if data_type == DataType.LIVE else Confidence.LOW,
                notes="Evergreen scoring reflects search interest, which leads but does not equal sales.",
                last_updated=utc_now_iso(),
            ).as_dict(),
        }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _seller_stage(reviews: int | None, threshold: int) -> str:
    if reviews is None:
        return "Unknown"
    if reviews <= threshold:
        return "New Seller"
    if reviews <= threshold * 6:
        return "Growing"
    if reviews <= threshold * 40:
        return "Established"
    return "Dominant"


def _new_seller_verdict(new_count: int, succeeding: int, total: int) -> str:
    if succeeding:
        return (
            f"{succeeding} of {total} listings are new sellers already hitting the volume target - "
            "the strongest possible sign that a newcomer can win here."
        )
    if new_count:
        return (
            f"{new_count} of {total} listings are new sellers, but none clear the volume target yet - "
            "entry is possible, traction is not proven."
        )
    return "No new sellers on the sampled page: every listing has an established review base. Hard entry."


def _volume_verdict(performers: int, total: int, target: int) -> str:
    if performers == 0:
        return f"No sampled listing clears {target} units/month - the demand may be too thin to be worth it."
    share = performers / total
    if share > 0.6:
        return f"{performers} of {total} listings clear {target} units/month - healthy, proven demand."
    if share > 0.3:
        return f"{performers} of {total} clear {target} units/month - real demand, concentrated in the leaders."
    return f"Only {performers} of {total} clear {target} units/month - demand sits with a small head of the market."


def _entry_verdict(
    profiles: list[CompetitorProfile], new_and_performing: list[CompetitorProfile], performers: list[CompetitorProfile]
) -> str:
    if not performers:
        return "Avoid: no listing is selling at a meaningful volume."
    if new_and_performing:
        return "Strong entry case: proven demand and at least one new seller already succeeding."
    dominant = sum(1 for p in profiles if p.seller_stage == "Dominant")
    if dominant > len(profiles) * 0.5:
        return "Hard entry: the page is dominated by listings with very large review bases."
    return "Workable entry: demand is proven, but you will need a differentiated listing to take share."
