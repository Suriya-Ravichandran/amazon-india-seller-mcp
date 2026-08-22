"""Amazon marketplace data access, risk assessment and listing composition.

The service owns a small provider abstraction so the same business logic can sit
on top of a demo dataset today and Amazon SP-API / PA-API / an approved
third-party data API tomorrow.

Nothing here ever claims demo output is live marketplace data: every result
carries a :class:`~services.DataEnvelope`.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Iterable

import httpx
from pydantic import BaseModel, Field

from amazon_india_seller_mcp.config.settings import Settings, get_settings
from amazon_india_seller_mcp.services import (
    Confidence,
    DataEnvelope,
    DataType,
    InsufficientDataError,
    InvalidInputError,
    ProviderNotConfiguredError,
    RateLimitError,
    ServiceError,
    TTLCache,
    deterministic_rng,
)

logger = logging.getLogger(__name__)

SUPPORTED_MARKETPLACES = {"amazon.in"}


# --------------------------------------------------------------------------- #
# Domain models
# --------------------------------------------------------------------------- #
class ProductListing(BaseModel):
    """One competitor listing on the marketplace."""

    title: str
    brand: str
    is_branded: bool = False
    price: float
    rating: float
    review_count: int
    bsr: int | None = None
    weight_grams: float | None = None
    category: str
    image_count: int = 5
    bullet_count: int = 5
    has_video: bool = False
    has_aplus_content: bool = False
    title_length: int = 0
    fulfillment: str = "FBA"
    listing_quality_score: float = 0.0
    quality_known: bool = True          # False when the source cannot see images/bullets
    asin: str | None = None
    bought_past_month: int | None = None
    image_urls: list[str] = Field(default_factory=list)
    is_sponsored: bool = False


class ProductSnapshot(BaseModel):
    """Aggregate marketplace view of a product idea."""

    product_name: str
    marketplace: str
    category: str
    price_min: float
    price_max: float
    price_avg: float
    bsr: int | None
    weight_grams: float
    rating: float
    review_count: int
    competitor_count: int
    listings: list[ProductListing] = Field(default_factory=list)
    envelope: DataEnvelope


class ReviewRecord(BaseModel):
    """A single customer review."""

    rating: int
    title: str
    body: str
    verified_purchase: bool = True
    helpful_votes: int = 0
    themes: list[str] = Field(default_factory=list)


class RiskProfile(BaseModel):
    """Beginner-seller risk assessment derived from product traits."""

    traits: list[str] = Field(default_factory=list)
    return_risk: str = "Low"
    return_rate_estimate: float = 0.04
    gated_category_risk: str = "Low"
    brand_approval_risk: str = "Low"
    sourcing_ease_score: float = 75.0
    beginner_score: float = 75.0
    penalties: list[str] = Field(default_factory=list)
    penalty_points: float = 0.0


# --------------------------------------------------------------------------- #
# Keyword driven classification tables
# --------------------------------------------------------------------------- #
CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Home & Kitchen": (
        "sink", "strainer", "kitchen", "spatula", "storage", "container", "bottle", "jar",
        "organizer", "organiser", "dispenser", "mop", "broom", "hanger", "rack", "chopper",
        "peeler", "silicone", "mat", "curtain", "towel", "basket", "lunch", "tiffin",
    ),
    "Mobile Accessories": ("cable", "charger", "usb", "phone", "mobile", "earphone", "screen guard", "power bank"),
    "Electronics Accessories": ("laptop", "keyboard", "mouse", "hdmi", "adapter", "led", "light", "bulb"),
    "Office Products": ("pen", "notebook", "file", "stapler", "desk", "stationery", "marker", "diary"),
    "Beauty": ("face", "serum", "cream", "makeup", "brush", "nail", "hair", "lipstick"),
    "Health & Personal Care": ("massager", "sanitizer", "thermometer", "supplement", "razor", "trimmer"),
    "Baby": ("baby", "infant", "diaper", "feeding", "toddler"),
    "Sports & Fitness": ("yoga", "gym", "resistance", "skipping", "dumbbell", "fitness", "cycling"),
    "Pet Supplies": ("pet", "dog", "cat", "aquarium", "leash"),
    "Toys": ("toy", "puzzle", "game", "doll", "block"),
    "Automotive Accessories": ("car", "bike", "helmet", "wiper", "tyre", "vehicle"),
    "Apparel": ("shirt", "tshirt", "t-shirt", "jeans", "dress", "saree", "kurta", "socks", "innerwear"),
    "Grocery": ("masala", "snack", "tea", "coffee", "atta", "rice", "oil", "spice"),
}

BRAND_TOKENS = (
    "boat", "mi ", "xiaomi", "samsung", "apple", "iphone", "oneplus", "prestige", "milton",
    "cello", "havells", "philips", "nike", "adidas", "puma", "lakme", "nivea", "bajaj",
    "usha", "wipro", "godrej", "realme", "oppo", "vivo", "sony", "jbl", "noise",
)

TRAIT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "fragile": ("glass", "ceramic", "mirror", "porcelain", "crystal", "bulb", "lamp shade"),
    "complex_electronics": ("earbuds", "smartwatch", "speaker", "camera", "router", "projector", "trimmer"),
    "battery": ("battery", "rechargeable", "power bank", "powerbank", "cordless", "li-ion"),
    "hazardous": ("aerosol", "pesticide", "lighter", "fuel", "flammable", "acid", "sanitizer"),
    "perishable": ("food", "edible", "snack", "juice", "dairy", "fresh", "grocery"),
    "seasonal": ("raincoat", "umbrella", "sweater", "woolen", "woollen", "diwali", "holi", "christmas", "cooler", "heater"),
    "apparel_sizing": ("shirt", "tshirt", "t-shirt", "jeans", "dress", "bra", "shoe", "footwear", "kurta"),
    "counterfeit_risk": ("replica", "first copy", "clone", "inspired"),
}

GATED_KEYWORDS: dict[str, tuple[str, ...]] = {
    "High": ("supplement", "medicine", "medical", "pesticide", "ayurvedic", "cosmetic", "drug", "sanitizer"),
    "Medium": ("food", "grocery", "toy", "baby", "cream", "serum", "helmet", "electrical"),
}

DAILY_USE_KEYWORDS = (
    "kitchen", "cleaning", "storage", "organizer", "organiser", "bathroom", "cable",
    "holder", "dispenser", "mop", "brush", "bag", "mat", "hook", "clip", "stand",
)

# A small curated demo catalogue for products beginner sellers commonly ask about.
DEMO_CATALOG: dict[str, dict[str, Any]] = {
    "silicone sink strainer": {"category": "Home & Kitchen", "price_avg": 249, "weight": 90, "bsr": 4200, "rating": 4.1, "reviews": 1850, "demand_index": 78},
    "cable organizer": {"category": "Mobile Accessories", "price_avg": 299, "weight": 120, "bsr": 3100, "rating": 4.2, "reviews": 3400, "demand_index": 84},
    "soap dispenser": {"category": "Home & Kitchen", "price_avg": 399, "weight": 260, "bsr": 2600, "rating": 3.9, "reviews": 5200, "demand_index": 86},
    "reusable silicone food storage bag": {"category": "Home & Kitchen", "price_avg": 549, "weight": 180, "bsr": 8800, "rating": 4.0, "reviews": 720, "demand_index": 64},
    "kitchen drawer organizer": {"category": "Home & Kitchen", "price_avg": 449, "weight": 380, "bsr": 5400, "rating": 4.0, "reviews": 1600, "demand_index": 72},
    "mobile stand": {"category": "Mobile Accessories", "price_avg": 249, "weight": 140, "bsr": 1900, "rating": 4.1, "reviews": 9100, "demand_index": 88},
    "spice container set": {"category": "Home & Kitchen", "price_avg": 599, "weight": 480, "bsr": 6100, "rating": 4.2, "reviews": 2100, "demand_index": 70},
    "laundry bag": {"category": "Home & Kitchen", "price_avg": 349, "weight": 220, "bsr": 7300, "rating": 4.0, "reviews": 1250, "demand_index": 66},
    "yoga mat strap": {"category": "Sports & Fitness", "price_avg": 299, "weight": 150, "bsr": 15200, "rating": 4.3, "reviews": 480, "demand_index": 52},
    "pet hair remover": {"category": "Pet Supplies", "price_avg": 399, "weight": 160, "bsr": 9400, "rating": 3.8, "reviews": 900, "demand_index": 61},
    "desk cable clip": {"category": "Office Products", "price_avg": 199, "weight": 70, "bsr": 5900, "rating": 4.2, "reviews": 1400, "demand_index": 69},
    "vegetable chopper": {"category": "Home & Kitchen", "price_avg": 649, "weight": 490, "bsr": 1400, "rating": 3.9, "reviews": 15600, "demand_index": 92},
}


# --------------------------------------------------------------------------- #
# Provider abstraction
# --------------------------------------------------------------------------- #
class ProductDataProvider(ABC):
    """Interface every marketplace data source must implement."""

    name: str = "abstract"
    data_type: DataType = DataType.DEMO
    confidence: Confidence = Confidence.LOW

    def envelope(self, notes: str | None = None) -> DataEnvelope:
        return DataEnvelope(
            source=self.name,
            data_type=self.data_type,
            confidence=self.confidence,
            notes=notes,
        )

    @abstractmethod
    async def search_listings(self, keyword: str, marketplace: str, limit: int) -> list[ProductListing]:
        """Return competitor listings for a keyword."""

    @abstractmethod
    async def fetch_reviews(self, product_name: str, marketplace: str, max_reviews: int) -> list[ReviewRecord]:
        """Return customer reviews for a product."""


class DemoProductDataProvider(ProductDataProvider):
    """Deterministic, clearly labelled sample data used when no API is configured.

    Output is stable for a given keyword (seeded RNG) so tests and repeated
    Claude Desktop calls agree with each other.
    """

    name = "Local Demo Provider"
    data_type = DataType.DEMO
    confidence = Confidence.LOW

    async def search_listings(self, keyword: str, marketplace: str, limit: int) -> list[ProductListing]:
        rng = deterministic_rng("listings", keyword, marketplace)
        profile = _catalog_profile(keyword)
        category = profile["category"]
        base_price = float(profile["price_avg"])
        base_reviews = int(profile["reviews"])
        listings: list[ProductListing] = []

        for index in range(max(1, limit)):
            branded = rng.random() < 0.35
            price = round(base_price * rng.uniform(0.72, 1.38), 0)
            reviews = max(3, int(base_reviews * rng.uniform(0.05, 1.6) / (1 + index * 0.12)))
            rating = round(min(4.8, max(3.1, float(profile["rating"]) + rng.uniform(-0.6, 0.5))), 1)
            image_count = rng.choice([3, 4, 5, 5, 6, 7])
            bullet_count = rng.choice([2, 3, 4, 5, 5, 5])
            listing = ProductListing(
                title=_demo_title(keyword, index, rng),
                brand=f"DemoBrand{index + 1}" if branded else "Generic Seller",
                is_branded=branded,
                price=price,
                rating=rating,
                review_count=reviews,
                bsr=max(120, int(profile["bsr"] * rng.uniform(0.4, 3.0))),
                weight_grams=round(float(profile["weight"]) * rng.uniform(0.7, 1.4), 0),
                category=category,
                image_count=image_count,
                bullet_count=bullet_count,
                has_video=rng.random() < 0.25,
                has_aplus_content=rng.random() < 0.3,
                title_length=rng.randint(45, 190),
                fulfillment=rng.choice(["FBA", "FBA", "Easy Ship", "Self Ship"]),
                asin=f"B0{rng.randint(10**7, 10**8 - 1)}",
                # Amazon shows this badge only on faster-moving listings.
                bought_past_month=(
                    rng.choice([50, 100, 200, 300, 500, 1000, 2000]) if rng.random() < 0.55 else None
                ),
                image_urls=[f"https://demo.invalid/{keyword.replace(' ', '-')}-{index}-{n}.jpg" for n in range(image_count)],
                is_sponsored=rng.random() < 0.2,
            )
            listing.listing_quality_score = _listing_quality(listing)
            listings.append(listing)

        listings.sort(key=lambda item: item.review_count, reverse=True)
        return listings

    async def fetch_reviews(self, product_name: str, marketplace: str, max_reviews: int) -> list[ReviewRecord]:
        rng = deterministic_rng("reviews", product_name, marketplace)
        category = _catalog_profile(product_name)["category"]
        templates = _review_templates(category)
        reviews: list[ReviewRecord] = []
        for _ in range(max(1, max_reviews)):
            positive = rng.random() < 0.63
            template = rng.choice(templates["positive"] if positive else templates["negative"])
            reviews.append(
                ReviewRecord(
                    rating=rng.choice([5, 5, 4]) if positive else rng.choice([1, 2, 3]),
                    title=template["title"],
                    body=template["body"].format(product=product_name),
                    verified_purchase=rng.random() < 0.85,
                    helpful_votes=rng.randint(0, 40),
                    themes=list(template["themes"]),
                )
            )
        return reviews


class HttpProductDataProvider(ProductDataProvider):
    """Generic HTTP provider for an approved third-party marketplace data API.

    The request/response shape differs per vendor, so the endpoint mapping below
    is intentionally minimal and expected to be adapted when a real provider is
    contracted.  It is wired end to end (auth header, timeout, error mapping) so
    that only the payload parsing needs changing.
    """

    data_type = DataType.LIVE
    confidence = Confidence.MEDIUM

    def __init__(self, settings: Settings) -> None:
        if not settings.product_data_api_key or not settings.product_data_base_url:
            raise ProviderNotConfiguredError(
                "Product data provider "
                f"'{settings.product_data_provider}' needs PRODUCT_DATA_API_KEY and PRODUCT_DATA_BASE_URL."
            )
        self.name = f"{settings.product_data_provider} API"
        self._base_url = settings.product_data_base_url.rstrip("/")
        self._api_key = settings.product_data_api_key
        self._timeout = settings.http_timeout_seconds

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(f"{self._base_url}{path}", params=params, headers=headers)
        except httpx.HTTPError as exc:
            logger.exception("Provider request failed")
            raise ServiceError(f"Product data provider request failed: {type(exc).__name__}") from exc

        if response.status_code == 429:
            raise RateLimitError("Product data provider rate limit exceeded.")
        if response.status_code in (401, 403):
            raise ProviderNotConfiguredError("Product data provider rejected the configured API credentials.")
        if response.status_code >= 400:
            raise ServiceError(f"Product data provider returned HTTP {response.status_code}.")
        return response.json()

    async def search_listings(self, keyword: str, marketplace: str, limit: int) -> list[ProductListing]:
        payload = await self._get("/search", {"keyword": keyword, "marketplace": marketplace, "limit": limit})
        items: Iterable[dict[str, Any]] = payload.get("results") or payload.get("items") or []
        listings = [_listing_from_payload(item, keyword) for item in items]
        if not listings:
            raise InsufficientDataError(f"Provider returned no listings for '{keyword}'.")
        return listings

    async def fetch_reviews(self, product_name: str, marketplace: str, max_reviews: int) -> list[ReviewRecord]:
        payload = await self._get(
            "/reviews", {"product": product_name, "marketplace": marketplace, "limit": max_reviews}
        )
        items: Iterable[dict[str, Any]] = payload.get("reviews") or []
        reviews = [
            ReviewRecord(
                rating=int(item.get("rating", 3)),
                title=str(item.get("title", "")),
                body=str(item.get("body", item.get("text", ""))),
                verified_purchase=bool(item.get("verified_purchase", False)),
                helpful_votes=int(item.get("helpful_votes", 0)),
            )
            for item in items
        ]
        if not reviews:
            raise InsufficientDataError(f"Provider returned no reviews for '{product_name}'.")
        return reviews


class ScraperProductDataProvider(ProductDataProvider):
    """Live Amazon India data from the public pages, via the guardrailed browser layer.

    This is the zero-API-key route to real data. It inherits every guardrail in
    :mod:`services.browser_service`: allowlist, robots.txt, crawl delay, page
    budget, and a hard stop when the site blocks the request.
    """

    name = "amazon.in public pages (scraped)"
    data_type = DataType.LIVE
    confidence = Confidence.MEDIUM

    def __init__(self, settings: Settings) -> None:
        from amazon_india_seller_mcp.services.scraper_service import AmazonScraperService  # noqa: PLC0415 - avoids a cycle

        self.settings = settings
        self.scraper = AmazonScraperService(settings)

    async def search_listings(self, keyword: str, marketplace: str, limit: int) -> list[ProductListing]:
        pages = 1 if limit <= 20 else 2
        payload = await self.scraper.scrape_search(keyword, pages=pages)
        category = infer_category(keyword)
        listings: list[ProductListing] = []
        for row in payload["listings"][:limit]:
            if not row.get("price"):
                continue  # No price means nothing downstream can be computed honestly.
            title = row.get("title") or keyword
            listings.append(
                ProductListing(
                    title=title,
                    brand=_brand_from_title(title),
                    is_branded=any(token in title.lower() for token in BRAND_TOKENS),
                    price=float(row["price"]),
                    rating=float(row.get("rating") or 0.0),
                    review_count=int(row.get("review_count") or 0),
                    bsr=None,                      # BSR lives on the product page, not the search page
                    weight_grams=None,
                    category=category,
                    image_count=1 if row.get("image_url") else 0,
                    bullet_count=0,
                    title_length=len(title),
                    fulfillment="Unknown",
                    quality_known=False,           # a search page cannot show images/bullets/A+
                    asin=row.get("asin"),
                    bought_past_month=row.get("bought_past_month"),
                    image_urls=[row["image_url"]] if row.get("image_url") else [],
                    is_sponsored=bool(row.get("is_sponsored")),
                )
            )
        if not listings:
            raise InsufficientDataError(
                f"No priced listings could be parsed from Amazon India for '{keyword}'."
            )
        return listings

    async def fetch_reviews(self, product_name: str, marketplace: str, max_reviews: int) -> list[ReviewRecord]:
        """Reviews need an ASIN, so find the top listing first, then read its reviews."""
        listings = await self.search_listings(product_name, marketplace, limit=5)
        asin = next((item.asin for item in listings if item.asin), None)
        if not asin:
            raise InsufficientDataError(f"Could not resolve an ASIN for '{product_name}'.")

        pages = max(1, min(10, (max_reviews + 9) // 10))
        payload = await self.scraper.scrape_reviews(asin, pages=pages)
        reviews = [
            ReviewRecord(
                rating=int(row.get("rating") or 3),
                title=row.get("title") or "",
                body=row.get("body") or "",
                verified_purchase=bool(row.get("verified_purchase")),
                helpful_votes=int(row.get("helpful_votes") or 0),
            )
            for row in payload["reviews"]
        ][:max_reviews]
        if not reviews:
            raise InsufficientDataError(
                f"Amazon returned no public reviews for ASIN {asin}. Review pages are often gated "
                "behind a signed-in session; use a licensed data provider for review analysis."
            )
        return reviews


def _brand_from_title(title: str) -> str:
    """Amazon India titles lead with the brand, so the first token is a fair guess."""
    first = (title or "").strip().split(" ")[0]
    return first if first and first[0].isupper() else "Unknown"


def build_provider(settings: Settings) -> ProductDataProvider:
    """Pick the provider implied by configuration (demo unless a real API is set)."""
    # DEMO_MODE is an absolute kill switch: it must never fall through to a
    # provider that touches the network, whatever else is configured.
    if settings.demo_mode:
        return DemoProductDataProvider()
    if settings.product_data_provider in {"scraper", "amazon-scraper", "browser"}:
        return ScraperProductDataProvider(settings)
    if settings.is_demo:
        return DemoProductDataProvider()
    if settings.product_data_provider in {"sp-api", "pa-api"}:
        raise ProviderNotConfiguredError(
            f"Provider '{settings.product_data_provider}' is not implemented yet. "
            "Implement a ProductDataProvider subclass in services/amazon_service.py, "
            "or set DEMO_MODE=true."
        )
    return HttpProductDataProvider(settings)


# --------------------------------------------------------------------------- #
# The service
# --------------------------------------------------------------------------- #
class AmazonService:
    """Marketplace research: snapshots, risk profiling, competition, reviews, listings."""

    def __init__(self, settings: Settings | None = None, provider: ProductDataProvider | None = None) -> None:
        self.settings = settings or get_settings()
        self._provider = provider
        self._cache = TTLCache(self.settings.cache_ttl_seconds, self.settings.cache_enabled)

    @property
    def provider(self) -> ProductDataProvider:
        if self._provider is None:
            self._provider = build_provider(self.settings)
        return self._provider

    # -- validation ------------------------------------------------------- #
    @staticmethod
    def validate_marketplace(marketplace: str) -> str:
        normalised = (marketplace or "amazon.in").strip().lower()
        if normalised not in SUPPORTED_MARKETPLACES:
            raise InvalidInputError(
                f"Invalid marketplace '{marketplace}'. Supported: {', '.join(sorted(SUPPORTED_MARKETPLACES))}.",
                remediation="Use marketplace='amazon.in'.",
            )
        return normalised

    # -- core data -------------------------------------------------------- #
    async def search_products(self, keyword: str, marketplace: str = "amazon.in", limit: int = 20) -> list[ProductListing]:
        """Return competitor listings for a keyword."""
        marketplace = self.validate_marketplace(marketplace)
        keyword = _require_text(keyword, "product_name")
        cache_key = f"search::{keyword}::{marketplace}::{limit}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        listings = await self.provider.search_listings(keyword, marketplace, limit)
        for listing in listings:
            if not listing.listing_quality_score:
                listing.listing_quality_score = _listing_quality(listing)
        self._cache.set(cache_key, listings)
        return listings

    async def get_product_snapshot(self, product_name: str, marketplace: str = "amazon.in") -> ProductSnapshot:
        """Aggregate price band, BSR, weight, rating and review volume for a product."""
        marketplace = self.validate_marketplace(marketplace)
        listings = await self.search_products(product_name, marketplace, limit=20)
        if not listings:
            raise InsufficientDataError(f"No marketplace data available for '{product_name}'.")

        prices = [item.price for item in listings]
        weights = [item.weight_grams for item in listings if item.weight_grams]
        ratings = [item.rating for item in listings]
        reviews = [item.review_count for item in listings]
        bsrs = [item.bsr for item in listings if item.bsr]

        return ProductSnapshot(
            product_name=product_name,
            marketplace=marketplace,
            category=listings[0].category,
            price_min=round(min(prices), 2),
            price_max=round(max(prices), 2),
            price_avg=round(sum(prices) / len(prices), 2),
            bsr=int(sum(bsrs) / len(bsrs)) if bsrs else None,
            weight_grams=round(sum(weights) / len(weights), 1) if weights else 250.0,
            rating=round(sum(ratings) / len(ratings), 2),
            review_count=int(sum(reviews) / len(reviews)),
            competitor_count=len(listings),
            listings=listings,
            envelope=self.provider.envelope(
                "Aggregated across the returned listing sample; BSR and weight are sample averages."
            ),
        )

    # -- risk / beginner fit ---------------------------------------------- #
    def assess_risk(self, product_name: str, snapshot: ProductSnapshot | None = None) -> RiskProfile:
        """Score beginner suitability and flag risky product traits."""
        text = product_name.lower()
        traits = [trait for trait, tokens in TRAIT_KEYWORDS.items() if any(tok in text for tok in tokens)]
        if any(token in text for token in BRAND_TOKENS):
            traits.append("branded")

        criteria = self.settings.beginner_criteria
        weight = snapshot.weight_grams if snapshot else 250.0
        price = snapshot.price_avg if snapshot else 349.0

        if weight > criteria.max_weight_grams:
            traits.append("heavy")
        if snapshot and _brand_dominance(snapshot.listings) > 0.6:
            traits.append("strong_brand_dominance")

        penalties: list[str] = []
        penalty_points = 0.0
        beginner = 100.0
        for trait in set(traits):
            weight_penalty = {
                "branded": 12,
                "counterfeit_risk": 20,
                "fragile": 14,
                "complex_electronics": 16,
                "battery": 14,
                "hazardous": 20,
                "perishable": 18,
                "seasonal": 12,
                "apparel_sizing": 15,
                "heavy": 10,
                "strong_brand_dominance": 12,
                "high_return": 12,
            }.get(trait, 8)
            beginner -= weight_penalty
            penalty_points += weight_penalty * 0.25
            penalties.append(f"{trait.replace('_', ' ').title()} (-{weight_penalty} beginner points)")

        # Positive signals.
        if criteria.min_selling_price_inr <= price <= criteria.max_selling_price_inr:
            beginner += 6
        else:
            penalties.append(f"Average price ₹{price:.0f} sits outside the ₹199-₹699 beginner band")
            beginner -= 8
        if weight <= criteria.max_weight_grams:
            beginner += 5
        if any(token in text for token in DAILY_USE_KEYWORDS):
            beginner += 5

        gated = _gated_risk(text)
        brand_risk = "High" if "branded" in traits else ("Medium" if "strong_brand_dominance" in traits else "Low")
        return_rate, return_risk = _return_risk(text, traits)
        if return_risk in {"High", "Very High"} and "high_return" not in traits:
            traits.append("high_return")

        sourcing = _sourcing_ease(text, traits)

        return RiskProfile(
            traits=sorted(set(traits)),
            return_risk=return_risk,
            return_rate_estimate=return_rate,
            gated_category_risk=gated,
            brand_approval_risk=brand_risk,
            sourcing_ease_score=sourcing,
            beginner_score=max(0.0, min(100.0, beginner)),
            penalties=penalties,
            penalty_points=round(min(20.0, penalty_points), 1),
        )

    # -- competition ------------------------------------------------------ #
    async def analyze_competition(
        self, keyword: str, marketplace: str = "amazon.in", max_competitors: int = 20
    ) -> dict[str, Any]:
        """Full competitive landscape analysis for a keyword."""
        marketplace = self.validate_marketplace(marketplace)
        if not 1 <= max_competitors <= 100:
            raise InvalidInputError(
                "max_competitors must be between 1 and 100.",
                remediation="Pass a value such as 20.",
            )
        listings = await self.search_products(keyword, marketplace, limit=max_competitors)
        if not listings:
            raise InsufficientDataError(f"No competitor listings found for '{keyword}'.")

        prices = [item.price for item in listings]
        ratings = [item.rating for item in listings]
        reviews = sorted(item.review_count for item in listings)
        avg_price = sum(prices) / len(prices)
        avg_rating = sum(ratings) / len(ratings)
        avg_reviews = sum(reviews) / len(reviews)
        median_reviews = reviews[len(reviews) // 2]
        brand_share = _brand_dominance(listings)
        price_spread = (max(prices) - min(prices)) / avg_price if avg_price else 0.0

        # Some sources (a scraped search page) cannot see images, bullets or A+
        # content. Score quality only over listings where it is actually known.
        rated = [item for item in listings if item.quality_known]
        quality_known = bool(rated)
        avg_quality = sum(item.listing_quality_score for item in rated) / len(rated) if rated else 50.0

        level, score = _competition_level(median_reviews, brand_share, avg_rating, avg_quality)
        weak = [
            {
                "title": item.title,
                "brand": item.brand,
                "price": item.price,
                "rating": item.rating,
                "review_count": item.review_count,
                "listing_quality_score": round(item.listing_quality_score, 1) if item.quality_known else None,
                "weakness": _weakness_reason(item),
            }
            for item in listings
            if (item.quality_known and item.listing_quality_score < 60) or item.rating < 3.9
        ][:8]

        return {
            "keyword": keyword,
            "marketplace": marketplace,
            "competitors_analysed": len(listings),
            "competition_level": level,
            "competition_score": round(score, 1),
            "average_competitor_price": round(avg_price, 2),
            "price_range": {"min": round(min(prices), 2), "max": round(max(prices), 2)},
            "average_rating": round(avg_rating, 2),
            "average_review_count": int(avg_reviews),
            "median_review_count": int(median_reviews),
            "brand_dominance": {
                "branded_share": round(brand_share, 2),
                "level": "High" if brand_share > 0.6 else "Medium" if brand_share > 0.3 else "Low",
            },
            "price_competition": {
                "spread_ratio": round(price_spread, 2),
                "level": "High" if price_spread > 0.8 else "Medium" if price_spread > 0.4 else "Low",
            },
            "review_barrier": {
                "median_reviews_to_compete": int(median_reviews),
                "level": "High" if median_reviews > 1500 else "Medium" if median_reviews > 400 else "Low",
            },
            "listing_quality": (
                {
                    "average_score": round(avg_quality, 1),
                    "level": "Strong" if avg_quality >= 75 else "Average" if avg_quality >= 55 else "Weak",
                    "listings_assessed": len(rated),
                }
                if quality_known
                else {
                    "average_score": None,
                    "level": "Unknown",
                    "note": "This data source cannot see images, bullets or A+ content. "
                    "Run scrape_amazon_product on individual ASINs to assess listing quality.",
                }
            ),
            "image_quality": (
                {
                    "average_images": round(sum(i.image_count for i in rated) / len(rated), 1),
                    "share_with_video": round(sum(1 for i in rated if i.has_video) / len(rated), 2),
                    "share_with_aplus": round(sum(1 for i in rated if i.has_aplus_content) / len(rated), 2),
                }
                if quality_known
                else {"note": "Not visible from this data source."}
            ),
            "purchase_signals": {
                "listings_with_bought_badge": sum(1 for i in listings if i.bought_past_month),
                "total_bought_past_month": sum(i.bought_past_month or 0 for i in listings) or None,
                "sponsored_share": round(sum(1 for i in listings if i.is_sponsored) / len(listings), 2),
            },
            "differentiation_opportunity": _differentiation_opportunity(avg_quality, brand_share, avg_rating),
            "weak_listings": weak,
            "opportunities": _competition_opportunities(listings, avg_price, avg_quality, brand_share, avg_rating),
            **self.provider.envelope("Competition metrics are computed from the returned listing sample.").as_dict(),
        }

    # -- reviews ---------------------------------------------------------- #
    async def analyze_reviews(
        self, product_name: str, marketplace: str = "amazon.in", max_reviews: int = 200
    ) -> dict[str, Any]:
        """Group customer reviews into complaint / praise themes with fixes."""
        marketplace = self.validate_marketplace(marketplace)
        if not 10 <= max_reviews <= 1000:
            raise InvalidInputError("max_reviews must be between 10 and 1000.", remediation="Try max_reviews=200.")
        reviews = await self.provider.fetch_reviews(product_name, marketplace, max_reviews)
        if not reviews:
            raise InsufficientDataError(f"No reviews available for '{product_name}'.")

        negative = [r for r in reviews if r.rating <= 3]
        positive = [r for r in reviews if r.rating >= 4]
        complaint_counts = _count_themes(negative)
        praise_counts = _count_themes(positive)

        complaints = [
            {
                "common_complaint": THEME_LABELS.get(theme, theme.replace("_", " ").title()),
                "theme": theme,
                "mentions": count,
                "share_of_negative_reviews": round(count / max(1, len(negative)), 2),
                "recommended_improvement": THEME_FIXES.get(theme, "Investigate this issue with your supplier before ordering."),
            }
            for theme, count in complaint_counts
        ]
        praised = [
            {
                "feature": THEME_LABELS.get(theme, theme.replace("_", " ").title()),
                "theme": theme,
                "mentions": count,
            }
            for theme, count in praise_counts
        ]

        def bucket(*themes: str) -> list[dict[str, Any]]:
            return [c for c in complaints if c["theme"] in themes]

        return {
            "product_name": product_name,
            "marketplace": marketplace,
            "reviews_analysed": len(reviews),
            "negative_reviews": len(negative),
            "positive_reviews": len(positive),
            "average_rating": round(sum(r.rating for r in reviews) / len(reviews), 2),
            "most_common_complaints": complaints[:8],
            "most_appreciated_features": praised[:6],
            "quality_problems": bucket("durability", "material_quality", "defective"),
            "packaging_problems": bucket("packaging"),
            "size_problems": bucket("size_fit"),
            "usability_problems": bucket("usability", "installation"),
            "defects": bucket("defective", "leaking"),
            "product_improvement_opportunities": [c["recommended_improvement"] for c in complaints[:5]],
            "recommended_differentiation": _differentiation_from_complaints(complaints),
            **self.provider.envelope("Review themes are keyword-clustered, not human-verified.").as_dict(),
        }

    # -- listing ---------------------------------------------------------- #
    def build_listing(
        self,
        product_name: str,
        features: list[str],
        target_keywords: list[str],
        target_market: str = "India",
    ) -> dict[str, Any]:
        """Compose an Amazon India ready listing from features and keywords."""
        product_name = _require_text(product_name, "product_name")
        if not features:
            raise ServiceError("At least one product feature is required.", remediation="Pass 2-5 short features.")

        keywords = [k.strip() for k in target_keywords if k.strip()] or [product_name.lower()]
        primary = keywords[0]
        secondary = keywords[1:5]
        category = _catalog_profile(product_name)["category"]
        brand = "YourBrand"

        title = _compose_title(brand, product_name, primary, features, secondary)
        bullets = _compose_bullets(product_name, features, keywords)
        description = _compose_description(product_name, features, keywords, target_market)
        backend = _backend_terms(product_name, keywords, category)

        return {
            "product_name": product_name,
            "target_market": target_market,
            "category": category,
            "seo_optimized_title": title,
            "title_length": len(title),
            "alternative_title_ideas": _alternative_titles(brand, product_name, keywords, features),
            "bullet_points": bullets,
            "product_description": description,
            "backend_search_terms": backend,
            "backend_search_terms_byte_length": len(" ".join(backend).encode("utf-8")),
            "keyword_strategy": {
                "primary_keyword": primary,
                "secondary_keywords": secondary,
                "placement": {
                    "title": "Primary keyword within the first 80 characters.",
                    "bullets": "One secondary keyword per bullet, written naturally.",
                    "description": "Long-tail and problem/solution phrasing.",
                    "backend": "Synonyms, Hinglish spellings and misspellings only - never repeat title words.",
                },
            },
            "main_image_requirements": [
                "Pure white background (RGB 255,255,255), product fills ~85% of the frame.",
                "Minimum 1600x1600 px so zoom is enabled; square 1:1 preferred.",
                "No text, watermark, logo overlay, props or packaging in the main image.",
                "Show the actual product with true colour; no lifestyle background.",
            ],
            "lifestyle_image_ideas": [
                f"{product_name} in use in a typical Indian kitchen or home setting.",
                "Hands-in-frame shot showing scale and everyday use.",
                "Before / after shot showing the problem it solves.",
            ],
            "infographic_image_ideas": [
                f"Dimensions and weight callouts for {product_name}.",
                "Material and safety callouts (BPA-free, food grade) with icons.",
                f"Three-step 'how to use' strip for {product_name}.",
                "What's-in-the-box flat lay.",
            ],
            "product_comparison_image_ideas": [
                "Comparison table vs ordinary alternatives (durability, material, warranty).",
                "Variant / size comparison chart if you plan multiple SKUs.",
            ],
            "packaging_recommendations": [
                "Poly bag or small corrugated box with suffocation warning label (Amazon requirement).",
                "Print the FNSKU label flat and scannable; avoid curved surfaces.",
                "Keep packed weight under 500 g to stay in the lowest fulfilment fee slab.",
                "Add a thank-you insert requesting a review - never offer incentives (policy violation).",
            ],
            **DataEnvelope.estimated(
                "Rule-based listing composer",
                Confidence.MEDIUM,
                "Generated copy: verify claims (BPA-free, food grade, warranty) before publishing.",
            ).as_dict(),
        }


# --------------------------------------------------------------------------- #
# Module level helpers
# --------------------------------------------------------------------------- #
def _require_text(value: str, field: str) -> str:
    text = (value or "").strip()
    if len(text) < 2:
        raise InvalidInputError(f"Invalid {field}: provide at least 2 characters.")
    if len(text) > 200:
        raise InvalidInputError(f"Invalid {field}: keep it under 200 characters.")
    return text


def infer_category(product_name: str) -> str:
    """Best-effort category inference from the product keyword."""
    text = product_name.lower()
    best, best_hits = "Home & Kitchen", 0
    for category, tokens in CATEGORY_KEYWORDS.items():
        hits = sum(1 for token in tokens if token in text)
        if hits > best_hits:
            best, best_hits = category, hits
    return best


def _catalog_profile(product_name: str) -> dict[str, Any]:
    """Look up a curated demo profile, else synthesise a deterministic one."""
    text = product_name.strip().lower()
    if text in DEMO_CATALOG:
        return DEMO_CATALOG[text]
    for name, profile in DEMO_CATALOG.items():
        if name in text or text in name:
            return profile
    rng = deterministic_rng("profile", text)
    return {
        "category": infer_category(text),
        "price_avg": rng.choice([199, 249, 299, 349, 399, 449, 499, 599, 649]),
        "weight": rng.choice([80, 120, 180, 240, 320, 420, 480]),
        "bsr": rng.randint(1_200, 45_000),
        "rating": round(rng.uniform(3.6, 4.4), 1),
        "reviews": rng.randint(120, 6_000),
        "demand_index": rng.randint(35, 88),
    }


def demand_index_for(product_name: str) -> int:
    """Baseline 0-100 demand index used by the trends service."""
    return int(_catalog_profile(product_name)["demand_index"])


def _demo_title(keyword: str, index: int, rng) -> str:
    prefixes = ["Premium", "Heavy Duty", "Multipurpose", "Foldable", "Portable", "Pack of 2", "Pack of 4"]
    suffixes = ["for Home & Kitchen", "for Office Use", "- BPA Free", "with Storage Case", "(Assorted Colour)"]
    return f"{rng.choice(prefixes)} {keyword.title()} {rng.choice(suffixes)}".strip()


def _listing_quality(listing: ProductListing) -> float:
    """0-100 heuristic listing quality from images, bullets, title and content.

    Returns 0.0 for sources that cannot see listing content; callers check
    ``quality_known`` rather than treating that as a genuinely weak listing.
    """
    if not listing.quality_known:
        return 0.0
    score = 0.0
    score += min(35.0, listing.image_count * 5.0)
    score += min(25.0, listing.bullet_count * 5.0)
    score += 15.0 if 80 <= listing.title_length <= 200 else 5.0
    score += 12.0 if listing.has_video else 0.0
    score += 13.0 if listing.has_aplus_content else 0.0
    return round(min(100.0, score), 1)


def _brand_dominance(listings: list[ProductListing]) -> float:
    if not listings:
        return 0.0
    return sum(1 for item in listings if item.is_branded) / len(listings)


def _competition_level(median_reviews: float, brand_share: float, avg_rating: float, avg_quality: float) -> tuple[str, float]:
    """Return ``(label, competition_score)`` where a high score = friendlier market."""
    pressure = 0.0
    pressure += min(40.0, (median_reviews / 2500) * 40)
    pressure += brand_share * 25
    pressure += max(0.0, (avg_rating - 3.8)) * 15
    pressure += max(0.0, (avg_quality - 50) / 50) * 20
    pressure = min(100.0, pressure)

    if pressure < 20:
        label = "Low"
    elif pressure < 35:
        label = "Medium-Low"
    elif pressure < 50:
        label = "Medium"
    elif pressure < 65:
        label = "Medium-High"
    elif pressure < 80:
        label = "High"
    else:
        label = "Very High"
    return label, round(100 - pressure, 1)


def _weakness_reason(listing: ProductListing) -> str:
    reasons = []
    if not listing.quality_known:
        return f"weak rating {listing.rating}" if listing.rating < 3.9 else "listing content not visible from this source"
    if listing.image_count < 5:
        reasons.append(f"only {listing.image_count} images")
    if listing.bullet_count < 5:
        reasons.append(f"only {listing.bullet_count} bullet points")
    if not listing.has_aplus_content:
        reasons.append("no A+ content")
    if listing.rating < 3.9:
        reasons.append(f"weak rating {listing.rating}")
    if listing.title_length < 80:
        reasons.append("short, keyword-poor title")
    return ", ".join(reasons) or "below-average overall listing quality"


def _differentiation_opportunity(avg_quality: float, brand_share: float, avg_rating: float) -> str:
    if avg_quality < 55 and brand_share < 0.4:
        return "High - weak listings and few established brands leave room for a better-presented product."
    if avg_rating < 3.9:
        return "High - low average rating means unresolved customer complaints you can fix."
    if brand_share > 0.6:
        return "Low - established brands dominate; differentiation alone may not be enough."
    return "Medium - improve imagery, bundle value and review velocity to stand out."


def _competition_opportunities(
    listings: list[ProductListing], avg_price: float, avg_quality: float, brand_share: float, avg_rating: float
) -> dict[str, Any]:
    return {
        "negative_review_opportunities": (
            "Average rating is below 4.0 - mine 1-3 star reviews with analyze_reviews and fix the top complaint."
            if avg_rating < 4.0
            else "Ratings are healthy - differentiate on bundle and presentation rather than defect fixing."
        ),
        "bundle_opportunities": [
            "Sell as a multi-pack (2 or 4) to lift order value above the lowest fee slab.",
            "Bundle a complementary low-cost accessory to escape direct price comparison.",
            f"Test a value bundle priced around ₹{round(avg_price * 1.35):.0f} against single units at ₹{round(avg_price):.0f}.",
        ],
        "product_improvement_opportunities": [
            "Upgrade the weakest material component named in negative reviews.",
            "Add clear size and dimension callouts to reduce returns.",
            "Improve retail packaging so the unboxing justifies a premium price.",
        ],
        "keyword_opportunities": [
            "Target long-tail phrases the top listings omit from their titles.",
            "Add Hinglish and regional spellings in backend search terms.",
        ],
        "listing_gap_summary": (
            (
                f"{sum(1 for i in listings if i.quality_known and i.image_count < 5)} of "
                f"{sum(1 for i in listings if i.quality_known)} assessable listings have fewer than 5 images; "
                f"{sum(1 for i in listings if i.quality_known and not i.has_aplus_content)} have no A+ content."
            )
            if any(i.quality_known for i in listings)
            else "Listing content is not visible from this data source."
        ),
        "brand_dominance_note": (
            "Branded sellers hold the majority of the first page." if brand_share > 0.6
            else "Generic sellers hold most of the first page - a good sign for a new private label."
        ),
    }


def _gated_risk(text: str) -> str:
    for level, tokens in GATED_KEYWORDS.items():
        if any(token in text for token in tokens):
            return level
    return "Low"


def _return_risk(text: str, traits: list[str]) -> tuple[float, str]:
    rate = 0.035
    if "apparel_sizing" in traits:
        rate += 0.09
    if "fragile" in traits:
        rate += 0.05
    if "complex_electronics" in traits or "battery" in traits:
        rate += 0.045
    if any(token in text for token in ("set", "combo", "kit")):
        rate += 0.01
    label = "Low" if rate < 0.05 else "Medium" if rate < 0.08 else "High" if rate < 0.12 else "Very High"
    return round(rate, 3), label


def _sourcing_ease(text: str, traits: list[str]) -> float:
    score = 80.0
    if any(token in text for token in ("silicone", "plastic", "steel", "cotton", "organizer", "holder", "stand")):
        score += 8
    if "complex_electronics" in traits:
        score -= 25
    if "battery" in traits:
        score -= 20
    if "hazardous" in traits or "perishable" in traits:
        score -= 25
    if "branded" in traits:
        score -= 15
    if "fragile" in traits:
        score -= 10
    return max(5.0, min(100.0, score))


def _listing_from_payload(item: dict[str, Any], keyword: str) -> ProductListing:
    """Map a third-party provider payload onto :class:`ProductListing`."""
    title = str(item.get("title") or keyword)
    brand = str(item.get("brand") or "Unknown")
    listing = ProductListing(
        title=title,
        brand=brand,
        is_branded=bool(item.get("is_branded", brand.lower() not in {"unknown", "generic"})),
        price=float(item.get("price") or item.get("current_price") or 0.0),
        rating=float(item.get("rating") or 0.0),
        review_count=int(item.get("review_count") or item.get("ratings_total") or 0),
        bsr=item.get("bsr") or item.get("sales_rank"),
        weight_grams=item.get("weight_grams"),
        category=str(item.get("category") or infer_category(keyword)),
        image_count=int(item.get("image_count") or 0),
        bullet_count=int(item.get("bullet_count") or 0),
        has_video=bool(item.get("has_video", False)),
        has_aplus_content=bool(item.get("has_aplus_content", False)),
        title_length=len(title),
        fulfillment=str(item.get("fulfillment") or "Unknown"),
    )
    listing.listing_quality_score = _listing_quality(listing)
    return listing


# -- review theming --------------------------------------------------------- #
THEME_LABELS = {
    "durability": "Stops working / breaks after short use",
    "material_quality": "Material feels cheap or thin",
    "packaging": "Packaging damaged or inadequate",
    "size_fit": "Size smaller or larger than expected",
    "usability": "Difficult or inconvenient to use",
    "installation": "Hard to install or assemble",
    "defective": "Arrived defective or not working",
    "leaking": "Leaks during use",
    "value": "Overpriced for what you get",
    "smell": "Strong chemical smell on arrival",
    "quality_good": "Good build quality",
    "value_good": "Good value for money",
    "easy_to_use": "Easy to use",
    "looks_good": "Looks good / neat design",
    "fits_well": "Fits as described",
}

THEME_FIXES = {
    "durability": "Specify a higher-grade internal component (e.g. reinforced stainless steel spring) and run a 1,000-cycle durability test on samples before bulk ordering.",
    "material_quality": "Move up one material grade (thicker silicone / higher GSM) and state the exact grade on the listing.",
    "packaging": "Switch to a double-wall corrugated box with an inner insert; run an Amazon ISTA-6 style drop test on samples.",
    "size_fit": "Add a dimension infographic with a real-world scale reference and put exact measurements in bullet 1.",
    "usability": "Redesign the touchpoint customers complain about and include a one-page pictorial usage guide.",
    "installation": "Ship pre-assembled where possible and include mounting hardware plus a QR code to a fitting video.",
    "defective": "Add a 100% pre-shipment functional check clause to your supplier PO and inspect a sample lot on arrival.",
    "leaking": "Upgrade the gasket/seal and require a water-tightness test report for every production batch.",
    "value": "Either bundle an accessory to raise perceived value or reposition the price against the market average.",
    "smell": "Require food-grade material certification and an extended curing/airing period before packing.",
}


def _review_templates(category: str) -> dict[str, list[dict[str, Any]]]:
    """Category-flavoured demo review templates."""
    negative = [
        {"title": "Stopped working", "body": "The {product} stopped working within three weeks of daily use.", "themes": ["durability"]},
        {"title": "Cheap material", "body": "Material of this {product} feels very thin and cheap for the price.", "themes": ["material_quality", "value"]},
        {"title": "Damaged packet", "body": "Packet was crushed, {product} arrived with a crack.", "themes": ["packaging", "defective"]},
        {"title": "Smaller than expected", "body": "Much smaller than the photos suggest, check the size before buying this {product}.", "themes": ["size_fit"]},
        {"title": "Hard to use", "body": "Difficult to operate, the {product} needs too much force every time.", "themes": ["usability"]},
        {"title": "Leaks", "body": "Water leaks from the joint of this {product} after a few uses.", "themes": ["leaking", "durability"]},
        {"title": "Bad smell", "body": "Strong chemical smell from the {product} even after washing.", "themes": ["smell", "material_quality"]},
        {"title": "Not worth the price", "body": "Overpriced for the quality, local market sells the same {product} cheaper.", "themes": ["value"]},
        {"title": "Installation trouble", "body": "No instructions provided, took an hour to fit the {product}.", "themes": ["installation"]},
        {"title": "Defective piece", "body": "Received a defective {product}, had to return it immediately.", "themes": ["defective"]},
    ]
    positive = [
        {"title": "Good quality", "body": "Build quality of this {product} is solid, using it daily without issues.", "themes": ["quality_good"]},
        {"title": "Value for money", "body": "Great value for money, the {product} does exactly what it promises.", "themes": ["value_good"]},
        {"title": "Very easy to use", "body": "Simple and convenient, anyone in the family can use this {product}.", "themes": ["easy_to_use"]},
        {"title": "Looks great", "body": "Looks neat and premium, the {product} matches my kitchen well.", "themes": ["looks_good"]},
        {"title": "Perfect fit", "body": "Size is exactly as described, the {product} fits perfectly.", "themes": ["fits_well"]},
        {"title": "Sturdy", "body": "Sturdier than expected, no bending or cracking so far with this {product}.", "themes": ["quality_good"]},
    ]
    if category in {"Mobile Accessories", "Electronics Accessories"}:
        negative.append({"title": "Loose fitting", "body": "The {product} does not grip properly and slips off.", "themes": ["usability", "material_quality"]})
    return {"negative": negative, "positive": positive}


def _count_themes(reviews: list[ReviewRecord]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for review in reviews:
        for theme in review.themes or _themes_from_text(f"{review.title} {review.body}"):
            counts[theme] = counts.get(theme, 0) + 1
    return sorted(counts.items(), key=lambda item: item[1], reverse=True)


def _themes_from_text(text: str) -> list[str]:
    """Fallback keyword clustering for providers that return raw review text."""
    lowered = text.lower()
    mapping = {
        "durability": ("broke", "broken", "stopped working", "not working after", "damaged after"),
        "material_quality": ("cheap", "thin", "flimsy", "poor quality", "plastic quality"),
        "packaging": ("packaging", "packet", "box was", "crushed"),
        "size_fit": ("small", "smaller", "large", "size", "fit"),
        "usability": ("difficult", "hard to", "inconvenient", "not easy"),
        "installation": ("install", "assemble", "fitting", "instructions"),
        "defective": ("defective", "faulty", "not working", "dead on arrival"),
        "leaking": ("leak", "leaking", "spill"),
        "value": ("overpriced", "waste of money", "not worth"),
        "smell": ("smell", "odour", "odor"),
        "quality_good": ("good quality", "sturdy", "well made", "solid"),
        "value_good": ("value for money", "worth the price", "cheap and good"),
        "easy_to_use": ("easy to use", "convenient", "simple"),
        "looks_good": ("looks good", "premium look", "beautiful", "neat"),
        "fits_well": ("perfect fit", "fits perfectly", "exact size"),
    }
    return [theme for theme, tokens in mapping.items() if any(token in lowered for token in tokens)]


def _differentiation_from_complaints(complaints: list[dict[str, Any]]) -> list[str]:
    if not complaints:
        return ["No dominant complaint cluster found - differentiate on presentation and bundle value."]
    top = complaints[0]
    lines = [
        f"Lead your listing on solving: {top['common_complaint']} ({top['mentions']} mentions).",
        "Put the fix in bullet 1 and in a comparison infographic against ordinary alternatives.",
    ]
    if len(complaints) > 1:
        lines.append(f"Second priority: {complaints[1]['common_complaint']} - address it in bullet 2.")
    return lines


# -- listing composition ---------------------------------------------------- #
def _titlecase_keyword(keyword: str) -> str:
    return " ".join(word.capitalize() if word.islower() else word for word in keyword.split())


def _compose_title(brand: str, product_name: str, primary: str, features: list[str], secondary: list[str]) -> str:
    """Build an Amazon India title: brand + primary keyword inside the first 80 chars."""
    head = f"{brand} {_titlecase_keyword(primary)}"
    if product_name.lower() not in primary.lower():
        head = f"{head} - {product_name}"
    benefit = ", ".join(features[:2])
    tail = f"{_titlecase_keyword(secondary[0])}" if secondary else "for Home & Kitchen"
    title = f"{head} | {benefit} | {tail}"
    return title[:200].rstrip(" |,-")


def _alternative_titles(brand: str, product_name: str, keywords: list[str], features: list[str]) -> list[str]:
    feature_blob = ", ".join(features[:3])
    ideas = [
        f"{brand} {product_name} - {feature_blob} | Ideal for Indian Kitchens",
        f"{_titlecase_keyword(keywords[0])} by {brand} | {features[0] if features else 'Durable'} & Reusable | Pack of 1",
        f"{brand} {product_name} with {features[-1] if features else 'Premium Finish'} - {_titlecase_keyword(keywords[-1])}",
    ]
    return [idea[:200] for idea in ideas]


def _compose_bullets(product_name: str, features: list[str], keywords: list[str]) -> list[str]:
    """Five benefit-led bullets, each seeded with a keyword where one is available."""
    padded = (features + [
        "Easy to clean and maintain",
        "Space saving compact design",
        "Suitable for daily household use",
        "Backed by responsive seller support",
        "Thoughtfully packed for safe delivery",
    ])[:5]
    headers = ["BUILT TO LAST", "EVERYDAY CONVENIENCE", "SMART DESIGN", "SAFE & RELIABLE", "WHAT YOU GET"]
    bullets: list[str] = []
    for index, feature in enumerate(padded):
        keyword = keywords[index % len(keywords)]
        bullets.append(
            f"{headers[index]}: {feature} - this {product_name.lower()} works as a dependable "
            f"{keyword} for everyday Indian homes, so you get consistent results without extra effort."
        )
    return bullets


def _compose_description(product_name: str, features: list[str], keywords: list[str], market: str) -> str:
    feature_lines = "\n".join(f"- {feature}" for feature in features)
    return (
        f"Looking for a reliable {keywords[0]}? The {product_name} is designed for everyday use in "
        f"{market} homes, where convenience and durability matter more than gimmicks.\n\n"
        f"Why customers choose it:\n{feature_lines}\n\n"
        f"Made for daily use, the {product_name.lower()} is easy to clean, easy to store and built to keep "
        f"performing after months of regular handling. Whether you use it in the kitchen, the bathroom or at "
        f"your desk, it stays practical and low maintenance.\n\n"
        f"Note: colours and packaging may vary slightly. If anything is not right with your order, contact us "
        f"through Amazon and we will help you sort it out.\n\n"
        f"Add the {product_name} to your cart today."
    )


def _backend_terms(product_name: str, keywords: list[str], category: str) -> list[str]:
    """Backend search terms: synonyms, Hinglish spellings and misspellings only."""
    base_tokens = {token for keyword in keywords for token in keyword.lower().split()}
    base_tokens |= set(product_name.lower().split())
    extras = {
        "Home & Kitchen": ["kitchen ka saman", "rasoi", "ghar ke liye", "household item"],
        "Mobile Accessories": ["mobile ka saman", "charger holder", "wire holder", "tar organizer"],
        "Office Products": ["office use", "study table", "desk saman", "stationary"],
    }.get(category, ["daily use", "multipurpose", "ghar ke liye"])
    terms = sorted(base_tokens) + extras + ["durable", "reusable", "gift item", "combo pack"]
    # Amazon caps backend search terms at 250 bytes; trim to fit.
    selected: list[str] = []
    used = 0
    for term in terms:
        cost = len(term.encode("utf-8")) + 1
        if used + cost > 249:
            break
        selected.append(term)
        used += cost
    return selected
