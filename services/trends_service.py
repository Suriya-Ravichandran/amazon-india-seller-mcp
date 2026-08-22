"""Search interest, demand estimation, seasonality and keyword research.

When ``GOOGLE_TRENDS_ENABLED`` is off (the default) the service uses an internal
estimation model whose output is always labelled ``Estimated`` or ``Demo`` -
never ``Live``.
"""

from __future__ import annotations

import logging
from typing import Any

from config.settings import Settings, get_settings
from services import (
    Confidence,
    DataEnvelope,
    DataType,
    InvalidInputError,
    ServiceError,
    TTLCache,
    deterministic_rng,
)
from services.amazon_service import (
    ProductSnapshot,
    demand_index_for,
    infer_category,
)

logger = logging.getLogger(__name__)

DEMAND_BANDS: tuple[tuple[int, str], ...] = (
    (85, "Very High"),
    (68, "High"),
    (45, "Medium"),
    (25, "Low"),
    (0, "Very Low"),
)

# Keyword -> (season label, peak months, seasonality risk)
SEASONAL_PATTERNS: dict[str, tuple[str, list[str], str]] = {
    "umbrella": ("Monsoon", ["Jun", "Jul", "Aug", "Sep"], "High"),
    "raincoat": ("Monsoon", ["Jun", "Jul", "Aug"], "High"),
    "sweater": ("Winter", ["Nov", "Dec", "Jan"], "High"),
    "woolen": ("Winter", ["Nov", "Dec", "Jan"], "High"),
    "heater": ("Winter", ["Nov", "Dec", "Jan"], "Very High"),
    "cooler": ("Summer", ["Mar", "Apr", "May", "Jun"], "Very High"),
    "sunscreen": ("Summer", ["Mar", "Apr", "May"], "Medium"),
    "diwali": ("Festive", ["Oct", "Nov"], "Very High"),
    "holi": ("Festive", ["Mar"], "Very High"),
    "rakhi": ("Festive", ["Aug"], "Very High"),
    "christmas": ("Festive", ["Dec"], "Very High"),
    "school": ("Academic", ["Apr", "May", "Jun"], "Medium"),
    "gift": ("Festive-leaning", ["Oct", "Nov", "Dec"], "Medium"),
}

TREND_TOKENS: dict[str, tuple[str, ...]] = {
    "Rising": ("silicone", "reusable", "eco", "organizer", "organiser", "cordless", "foldable", "smart"),
    "Declining": ("cd", "dvd", "wired earphone", "fax", "landline", "aux"),
}

INTENT_HINTS: tuple[tuple[str, str], ...] = (
    ("best", "Commercial - comparison shopper close to buying"),
    ("cheap", "Transactional - price sensitive buyer"),
    ("price", "Transactional - price sensitive buyer"),
    ("how to", "Informational - researching a solution"),
    ("for ", "Commercial - use-case specific buyer"),
    ("online", "Transactional - ready to purchase"),
)


class TrendsService:
    """Estimate demand level, trend direction, seasonality and keyword sets."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._cache = TTLCache(self.settings.cache_ttl_seconds, self.settings.cache_enabled)

    # -- provenance ------------------------------------------------------- #
    def _envelope(self, extra_note: str | None = None) -> DataEnvelope:
        notes: list[str] = []
        if self.settings.google_trends_enabled:
            notes.append(
                "GOOGLE_TRENDS_ENABLED is set but no live trends provider is implemented; "
                "results come from the internal estimation model."
            )
        if self.settings.is_demo:
            notes.append("Demo mode: values are deterministic samples, not real search data.")
        if extra_note:
            notes.append(extra_note)
        return DataEnvelope(
            source="Internal demand estimation model" if not self.settings.is_demo else "Local Demo Provider",
            data_type=DataType.DEMO if self.settings.is_demo else DataType.ESTIMATED,
            confidence=Confidence.LOW if self.settings.is_demo else Confidence.MEDIUM,
            notes=" ".join(notes) or None,
        )

    # -- demand ----------------------------------------------------------- #
    async def analyze_demand(
        self,
        product_name: str,
        marketplace: str = "amazon.in",
        snapshot: ProductSnapshot | None = None,
    ) -> dict[str, Any]:
        """Estimate demand level, trend direction, seasonality and monthly volume."""
        product_name = (product_name or "").strip()
        if len(product_name) < 2:
            raise InvalidInputError("Invalid product_name: provide at least 2 characters.")

        cache_key = f"demand::{product_name.lower()}::{marketplace}::{bool(snapshot)}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        rng = deterministic_rng("demand", product_name, marketplace)
        base_index = demand_index_for(product_name)

        # Marketplace signals sharpen the estimate when a snapshot is available.
        adjustment = 0.0
        signals: list[str] = ["Baseline category and keyword demand index"]
        if snapshot:
            if snapshot.bsr:
                adjustment += 12 if snapshot.bsr < 3_000 else 5 if snapshot.bsr < 10_000 else -6
                signals.append(f"Average BSR {snapshot.bsr:,} across the sampled listings")
            if snapshot.review_count > 2_000:
                adjustment += 8
                signals.append("High review volume among competitors indicates sustained sales")
            elif snapshot.review_count < 200:
                adjustment -= 6
                signals.append("Low review volume suggests a thin or immature market")

        demand_score = max(0.0, min(100.0, base_index + adjustment + rng.uniform(-3, 3)))
        demand_level = _band(demand_score)
        trend_direction, trend_note = _trend_direction(product_name, rng)
        season_label, peak_months, seasonality_risk = _seasonality(product_name)
        monthly_units = _monthly_demand(demand_score, snapshot)
        search_volume = _search_volume(demand_score)

        confidence = Confidence.LOW if self.settings.is_demo else (
            Confidence.MEDIUM if snapshot else Confidence.LOW
        )

        result = {
            "product": product_name,
            "marketplace": marketplace,
            "category": snapshot.category if snapshot else infer_category(product_name),
            "demand_level": demand_level,
            "demand_score": round(demand_score, 1),
            "trend_direction": trend_direction,
            "trend_note": trend_note,
            "seasonality": {
                "pattern": season_label,
                "peak_months": peak_months,
                "seasonality_risk": seasonality_risk,
                "non_seasonal": seasonality_risk in {"Low", "Very Low"},
            },
            "seasonality_risk": seasonality_risk,
            "estimated_monthly_search_interest": search_volume,
            "estimated_monthly_demand_units": monthly_units,
            "signals_used": signals,
            "confidence": confidence.value,
            **self._envelope(
                "Monthly demand is a modelled estimate, not measured Amazon sales data."
            ).as_dict(),
        }
        self._cache.set(cache_key, result)
        return result

    # -- keywords --------------------------------------------------------- #
    async def research_keywords(self, product_name: str, marketplace: str = "amazon.in") -> dict[str, Any]:
        """Build primary, secondary, long-tail, related and backend keyword sets."""
        product_name = (product_name or "").strip()
        if len(product_name) < 2:
            raise InvalidInputError("Invalid product_name: provide at least 2 characters.")

        rng = deterministic_rng("keywords", product_name, marketplace)
        base = product_name.lower()
        tokens = [token for token in base.split() if len(token) > 2]
        head = " ".join(tokens[-2:]) if len(tokens) >= 2 else base
        category = infer_category(base)

        primary = _dedupe([base, head, f"{head} online"])
        secondary = _dedupe([
            f"{base} for kitchen" if category == "Home & Kitchen" else f"{base} for home",
            f"{head} india",
            f"best {head}",
            f"{head} set",
            f"{head} pack of 2",
        ])
        long_tail = _dedupe([
            f"{base} for small kitchen",
            f"{base} under 500",
            f"best {base} for daily use",
            f"{head} for indian homes",
            f"{base} with warranty",
            f"heavy duty {head}",
        ])
        related = _dedupe([
            f"{token} accessories" for token in tokens[:2]
        ] + _category_related(category))

        keywords = [
            _keyword_row(term, "Primary", rng, index)
            for index, term in enumerate(primary)
        ] + [
            _keyword_row(term, "Secondary", rng, index + 10)
            for index, term in enumerate(secondary)
        ] + [
            _keyword_row(term, "Long-Tail", rng, index + 30)
            for index, term in enumerate(long_tail)
        ]
        keywords.sort(key=lambda row: row["priority_score"], reverse=True)

        backend = _dedupe(
            [term for term in related]
            + _hinglish_terms(category)
            + [f"{head} online india", "daily use", "multipurpose"]
        )

        return {
            "product_name": product_name,
            "marketplace": marketplace,
            "category": category,
            "primary_keywords": primary,
            "secondary_keywords": secondary,
            "long_tail_keywords": long_tail,
            "related_keywords": related,
            "keyword_table": keywords[:20],
            "keyword_priority": [row["keyword"] for row in keywords[:8]],
            "backend_search_terms": backend,
            "usage_recommendations": {
                "amazon_title": f"Lead with '{primary[0]}' inside the first 80 characters, add one secondary keyword after the benefit.",
                "bullet_points": "Use one secondary or long-tail keyword per bullet, written as natural benefit copy.",
                "description": "Weave long-tail and problem/solution phrases; keep paragraphs short for mobile readers.",
                "backend_search_terms": "Synonyms, Hinglish spellings and common misspellings only; never repeat title words; stay under 250 bytes.",
            },
            **self._envelope("Keyword volumes and priorities are modelled estimates, not Amazon search volume data.").as_dict(),
        }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _band(score: float) -> str:
    for threshold, label in DEMAND_BANDS:
        if score >= threshold:
            return label
    return "Very Low"


def _trend_direction(product_name: str, rng) -> tuple[str, str]:
    text = product_name.lower()
    for direction, tokens in TREND_TOKENS.items():
        if any(token in text for token in tokens):
            note = (
                "Keyword family associated with growing interest in Indian e-commerce."
                if direction == "Rising"
                else "Keyword family associated with structurally declining interest."
            )
            return direction, note
    direction = rng.choice(["Stable", "Stable", "Stable", "Rising", "Declining"])
    return direction, "No strong directional signal detected; treated as baseline behaviour."


def _seasonality(product_name: str) -> tuple[str, list[str], str]:
    text = product_name.lower()
    for token, (label, months, risk) in SEASONAL_PATTERNS.items():
        if token in text:
            return label, months, risk
    return "Year-round", ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], "Low"


def _monthly_demand(demand_score: float, snapshot: ProductSnapshot | None) -> int:
    """Rough monthly unit demand for the whole keyword, not for one seller."""
    units = int((demand_score ** 2) / 4.5)
    if snapshot and snapshot.bsr:
        units = int(units * (1.25 if snapshot.bsr < 5_000 else 0.85 if snapshot.bsr > 20_000 else 1.0))
    return max(30, units)


def _search_volume(demand_score: float) -> int:
    return max(200, int(demand_score * 380))


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.strip().lower()
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result


def _category_related(category: str) -> list[str]:
    return {
        "Home & Kitchen": ["kitchen organizer", "home essentials", "kitchen gadgets", "storage solution"],
        "Mobile Accessories": ["phone accessories", "desk cable management", "charging accessories"],
        "Office Products": ["desk organizer", "office essentials", "study table accessories"],
        "Sports & Fitness": ["home workout accessories", "gym essentials"],
        "Pet Supplies": ["pet grooming accessories", "pet cleaning"],
    }.get(category, ["daily use products", "household essentials"])


def _hinglish_terms(category: str) -> list[str]:
    return {
        "Home & Kitchen": ["kitchen ka saman", "rasoi ka saman", "ghar ke liye"],
        "Mobile Accessories": ["mobile ka saman", "tar organizer", "wire holder"],
        "Office Products": ["office ka saman", "study table saman"],
    }.get(category, ["ghar ke liye", "roj ke istemal ke liye"])


def _keyword_row(keyword: str, group: str, rng, salt: int) -> dict[str, Any]:
    """One scored keyword row (estimated volume, competition, intent, priority)."""
    local_rng = deterministic_rng("kwrow", keyword, str(salt))
    volume = {"Primary": local_rng.randint(3_000, 40_000), "Secondary": local_rng.randint(800, 9_000)}.get(
        group, local_rng.randint(90, 1_800)
    )
    competition = {"Primary": local_rng.uniform(0.55, 0.95), "Secondary": local_rng.uniform(0.3, 0.7)}.get(
        group, local_rng.uniform(0.1, 0.45)
    )
    intent = next((label for token, label in INTENT_HINTS if token in keyword), "Commercial - product seeker")
    priority = (volume ** 0.5) * (1.2 - competition)
    return {
        "keyword": keyword,
        "group": group,
        "estimated_monthly_searches": volume,
        "competition_index": round(competition, 2),
        "search_intent": intent,
        "priority_score": round(priority, 1),
        "priority": "High" if priority > 90 else "Medium" if priority > 35 else "Low",
        "data_type": DataType.ESTIMATED.value,
    }
