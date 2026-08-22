"""Search interest, demand estimation, seasonality and keyword research.

When ``GOOGLE_TRENDS_ENABLED`` is off (the default) the service uses an internal
estimation model whose output is always labelled ``Estimated`` or ``Demo`` -
never ``Live``.
"""

from __future__ import annotations

import logging
import statistics
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
    """Estimate demand level, trend direction, seasonality and keyword sets.

    With ``GOOGLE_TRENDS_ENABLED=true`` the service pulls **real** search
    interest from Google Trends (free, no API key, via ``pytrends``) and labels
    the result ``Live``. Without it, an internal model produces ``Estimated``
    values. The two are never confused for one another.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._cache = TTLCache(self.settings.cache_ttl_seconds, self.settings.cache_enabled)

    # -- live google trends ----------------------------------------------- #
    async def fetch_interest_over_time(
        self, keyword: str, timeframe: str = "today 12-m", geo: str = "IN"
    ) -> dict[str, Any] | None:
        """Fetch real Google Trends interest. Returns ``None`` when unavailable.

        Free and keyless, but unofficial: Google rate limits aggressively, so a
        failure here is normal and callers fall back to the internal model.
        """
        if not self.settings.google_trends_enabled:
            return None

        cache_key = f"gtrends::{keyword}::{timeframe}::{geo}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        import asyncio  # noqa: PLC0415

        def _run() -> dict[str, Any] | None:
            try:
                from pytrends.request import TrendReq  # noqa: PLC0415
            except ImportError:
                logger.warning(
                    "GOOGLE_TRENDS_ENABLED is true but pytrends is not installed "
                    "(`uv sync --extra realtime`); using the internal model instead."
                )
                return None
            try:
                client = TrendReq(hl="en-IN", tz=330, timeout=(10, 25))
                client.build_payload([keyword], timeframe=timeframe, geo=geo)
                frame = client.interest_over_time()
                if frame is None or frame.empty:
                    return None
                series = [int(value) for value in frame[keyword].tolist()]
                dates = [str(index.date()) for index in frame.index]
                partial = frame["isPartial"].tolist() if "isPartial" in frame else []
                # Drop a trailing partial week - it always looks like a crash.
                if partial and bool(partial[-1]) and len(series) > 1:
                    series, dates = series[:-1], dates[:-1]
                return {"keyword": keyword, "geo": geo, "timeframe": timeframe, "dates": dates, "series": series}
            except Exception as exc:  # noqa: BLE001 - unofficial endpoint, many failure modes
                logger.info("Google Trends unavailable for '%s': %s", keyword, type(exc).__name__)
                return None

        payload = await asyncio.to_thread(_run)
        if payload:
            self._cache.set(cache_key, payload)
        return payload

    # -- provenance ------------------------------------------------------- #
    def _envelope(self, extra_note: str | None = None, live: bool = False) -> DataEnvelope:
        notes: list[str] = []
        if live:
            notes.append("Search interest is live Google Trends data for India.")
        elif self.settings.google_trends_enabled:
            notes.append(
                "Google Trends was enabled but returned nothing (rate limit, no data, or pytrends "
                "not installed); these values come from the internal estimation model."
            )
        if self.settings.is_demo and not live:
            notes.append("Demo mode: values are deterministic samples, not real search data.")
        if extra_note:
            notes.append(extra_note)

        if live:
            source, data_type, confidence = "Google Trends (free, no API key)", DataType.LIVE, Confidence.MEDIUM
        elif self.settings.is_demo:
            source, data_type, confidence = "Local Demo Provider", DataType.DEMO, Confidence.LOW
        else:
            source, data_type, confidence = "Internal demand estimation model", DataType.ESTIMATED, Confidence.MEDIUM
        return DataEnvelope(source=source, data_type=data_type, confidence=confidence, notes=" ".join(notes) or None)

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

        # Real Google Trends data overrides the baseline when it is available.
        trends = await self.fetch_interest_over_time(product_name)
        live_series: list[int] | None = None
        if trends and trends["series"]:
            live_series = trends["series"]
            base_index = float(statistics.fmean(live_series))
            signals.append(
                f"Live Google Trends interest for India: {len(live_series)} points, "
                f"mean {base_index:.0f}/100 over {trends['timeframe']}"
            )
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

        jitter = 0.0 if live_series else rng.uniform(-3, 3)
        demand_score = max(0.0, min(100.0, base_index + adjustment + jitter))
        demand_level = _band(demand_score)
        if live_series:
            trend_direction, trend_note = _trend_from_series(live_series)
            season_label, peak_months, seasonality_risk = _seasonality_from_series(
                live_series, trends["dates"], product_name
            )
        else:
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
            "confidence": (Confidence.HIGH if live_series else confidence).value,
            "interest_over_time": (
                {
                    "source": "Google Trends (live, free, no API key)",
                    "geo": trends["geo"],
                    "timeframe": trends["timeframe"],
                    "dates": trends["dates"],
                    "series": live_series,
                }
                if live_series
                else None
            ),
            **self._envelope(
                "Monthly demand is a modelled estimate, not measured Amazon sales data.",
                live=bool(live_series),
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


def _trend_from_series(series: list[int]) -> tuple[str, str]:
    """Trend direction from the real interest series: recent half vs earlier half."""
    half = len(series) // 2
    earlier = statistics.fmean(series[:half]) or 1.0
    recent = statistics.fmean(series[half:])
    change = (recent - earlier) / earlier
    if change > 0.15:
        return "Rising", f"Live search interest is up {change * 100:.0f}% versus the earlier half of the period."
    if change < -0.15:
        return "Declining", f"Live search interest is down {abs(change) * 100:.0f}% versus the earlier half."
    return "Stable", f"Live search interest is flat ({change * 100:+.0f}% half over half)."


def _seasonality_from_series(
    series: list[int], dates: list[str], product_name: str
) -> tuple[str, list[str], str]:
    """Detect seasonality from the real series, grouping interest by calendar month."""
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    buckets: dict[int, list[int]] = {}
    for date_str, value in zip(dates, series):
        try:
            month = int(date_str.split("-")[1])
        except (IndexError, ValueError):
            continue
        buckets.setdefault(month, []).append(value)
    if len(buckets) < 6:
        return _seasonality(product_name)

    averages = {month: statistics.fmean(values) for month, values in buckets.items()}
    overall = statistics.fmean(averages.values()) or 1.0
    peak_ratio = max(averages.values()) / overall
    peak_months = [months[month - 1] for month, value in sorted(averages.items()) if value >= overall * 1.25]

    if peak_ratio >= 2.0:
        risk, label = "Very High", "Strongly seasonal"
    elif peak_ratio >= 1.5:
        risk, label = "High", "Seasonal"
    elif peak_ratio >= 1.25:
        risk, label = "Medium", "Mildly seasonal"
    else:
        risk, label = "Low", "Year-round"
    return label, peak_months or months, risk


def synthetic_interest_series(product_name: str, months: int = 36) -> list[int]:
    """A deterministic stand-in interest series for offline / demo use.

    Shaped by the same seasonality table the estimation model uses, so a
    seasonal keyword still looks seasonal. Clearly not real measurement.
    """
    rng = deterministic_rng("series", product_name, str(months))
    base = float(demand_index_for(product_name))
    _, peak_months, risk = _seasonality(product_name)
    peak_indexes = {
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"].index(month)
        for month in peak_months
        if month in ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    }
    amplitude = {"Very High": 1.6, "High": 1.0, "Medium": 0.45}.get(risk, 0.0)
    series: list[int] = []
    for index in range(months):
        month = index % 12
        seasonal = amplitude if month in peak_indexes and len(peak_indexes) < 12 else 0.0
        drift = (index / max(1, months)) * 0.12
        value = base * (1 + seasonal + drift) * rng.uniform(0.9, 1.1)
        series.append(int(max(1, min(100, value))))
    return series
