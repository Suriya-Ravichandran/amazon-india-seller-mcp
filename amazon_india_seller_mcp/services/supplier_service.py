"""Supplier and sourcing research for Indian sellers.

Data integrity rule for this service: **suppliers are never invented**.  When no
supplier data API is configured, the service returns an empty ``suppliers`` list
and instead points at *real, publicly known* sourcing channels (wholesale market
areas, manufacturing clusters, B2B directories) that the seller can verify for
themselves.  Nothing here presents a fabricated company name, phone number or
quotation as if it were a real supplier.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from pydantic import BaseModel, Field

from amazon_india_seller_mcp.config.settings import Settings, get_settings
from amazon_india_seller_mcp.services import (
    Confidence,
    DataEnvelope,
    DataType,
    InvalidInputError,
    RateLimitError,
    ServiceError,
    TTLCache,
)
from amazon_india_seller_mcp.services.amazon_service import infer_category

logger = logging.getLogger(__name__)

SUPPORTED_LOCATIONS = ("parrys", "chennai", "tamil nadu", "india")
SUPPORTED_SUPPLIER_TYPES = ("manufacturer", "wholesaler", "trader", "any")
VERIFICATION_STATUSES = ("Verified", "Public Listing", "Estimated", "Unverified")


class SupplierRecord(BaseModel):
    """A supplier or sourcing channel, always carrying its verification status."""

    supplier_name: str
    location: str
    supplier_type: str
    estimated_price: str | None = None
    minimum_order_quantity: str | None = None
    product_category: str
    website_or_source: str
    contact_available: bool = False
    sample_available: str = "Unknown"
    verification_status: str = "Unverified"
    notes: str | None = None


# Publicly known Indian sourcing channels. These are market areas, industrial
# clusters and B2B directories - not company records - and are marked as
# "Public Listing" so nobody mistakes them for a vetted supplier.
SOURCING_CHANNELS: list[dict[str, Any]] = [
    {
        "supplier_name": "Parry's Corner (George Town) wholesale market area",
        "location": "Parrys, Chennai, Tamil Nadu",
        "supplier_type": "Wholesale Market Area",
        "product_category": "General merchandise, plastics, stationery, household goods",
        "website_or_source": "Physical wholesale market, George Town, Chennai",
        "keywords": ("plastic", "household", "kitchen", "stationery", "general", "organizer", "storage", "toy"),
        "notes": "Traditional wholesale hub; deal in person, ask for GST invoice and take samples before bulk buying.",
    },
    {
        "supplier_name": "Ritchie Street electronics wholesale market",
        "location": "Mount Road area, Chennai, Tamil Nadu",
        "supplier_type": "Wholesale Market Area",
        "product_category": "Mobile and computer accessories, cables, small electronics",
        "website_or_source": "Physical wholesale market, Chennai",
        "keywords": ("cable", "mobile", "charger", "usb", "electronic", "laptop", "phone", "adapter"),
        "notes": "Verify BIS marking and warranty terms; counterfeit branded goods are a real risk here.",
    },
    {
        "supplier_name": "Sowcarpet / NSC Bose Road wholesale area",
        "location": "Sowcarpet, Chennai, Tamil Nadu",
        "supplier_type": "Wholesale Market Area",
        "product_category": "Home goods, hardware, packaging material, general items",
        "website_or_source": "Physical wholesale market, Chennai",
        "keywords": ("home", "hardware", "packaging", "bag", "cloth", "general", "kitchen"),
        "notes": "Good for packaging material and low-cost household SKUs.",
    },
    {
        "supplier_name": "Tiruppur knitwear manufacturing cluster",
        "location": "Tiruppur, Tamil Nadu",
        "supplier_type": "Manufacturing Cluster",
        "product_category": "Knitted garments, cotton textiles, cloth bags",
        "website_or_source": "Industrial cluster, Tiruppur",
        "keywords": ("cotton", "cloth", "garment", "tshirt", "t-shirt", "apparel", "bag", "textile", "towel"),
        "notes": "Export-grade knitwear cluster; MOQs are usually higher than trading markets.",
    },
    {
        "supplier_name": "Karur home textiles cluster",
        "location": "Karur, Tamil Nadu",
        "supplier_type": "Manufacturing Cluster",
        "product_category": "Home textiles, kitchen linen, table and bed linen",
        "website_or_source": "Industrial cluster, Karur",
        "keywords": ("towel", "napkin", "curtain", "bedsheet", "linen", "textile", "apron", "mat"),
        "notes": "Strong for kitchen and home linen SKUs with printing/embroidery options.",
    },
    {
        "supplier_name": "Coimbatore engineering and light manufacturing cluster",
        "location": "Coimbatore, Tamil Nadu",
        "supplier_type": "Manufacturing Cluster",
        "product_category": "Light engineering goods, pumps, metal fabrication, moulded parts",
        "website_or_source": "Industrial cluster, Coimbatore",
        "keywords": ("steel", "metal", "stand", "rack", "holder", "mould", "moulded", "tool"),
        "notes": "Useful for metal or moulded-plastic components and custom tooling.",
    },
    {
        "supplier_name": "IndiaMART B2B directory",
        "location": "India (nationwide)",
        "supplier_type": "B2B Directory",
        "product_category": "All categories",
        "website_or_source": "https://www.indiamart.com",
        "keywords": (),
        "notes": "Largest Indian B2B directory. Listings are seller-submitted - verify GSTIN, factory address and samples yourself.",
    },
    {
        "supplier_name": "TradeIndia B2B directory",
        "location": "India (nationwide)",
        "supplier_type": "B2B Directory",
        "product_category": "All categories",
        "website_or_source": "https://www.tradeindia.com",
        "keywords": (),
        "notes": "Seller-submitted listings; treat every quotation as unverified until you inspect samples.",
    },
    {
        "supplier_name": "Udaan wholesale app",
        "location": "India (nationwide)",
        "supplier_type": "Online Wholesaler",
        "product_category": "FMCG, general merchandise, electronics accessories",
        "website_or_source": "https://udaan.com",
        "keywords": (),
        "notes": "Lower MOQs than factories; good for first small test orders.",
    },
    {
        "supplier_name": "MSME-DI / District Industries Centre supplier lists",
        "location": "India (district level, incl. Tamil Nadu)",
        "supplier_type": "Government Directory",
        "product_category": "Registered MSME manufacturers",
        "website_or_source": "https://msme.gov.in and state DIC offices",
        "keywords": (),
        "notes": "Government-registered manufacturer lists - useful for verifying that a factory actually exists.",
    },
]


class SupplierService:
    """Supplier discovery with strict provenance rules."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._cache = TTLCache(self.settings.cache_ttl_seconds, self.settings.cache_enabled)

    # -- validation ------------------------------------------------------- #
    @staticmethod
    def validate_location(location: str) -> str:
        normalised = (location or "India").strip().lower()
        if normalised not in SUPPORTED_LOCATIONS:
            raise InvalidInputError(
                f"Unsupported location '{location}'.",
                remediation=f"Use one of: {', '.join(t.title() for t in SUPPORTED_LOCATIONS)}.",
            )
        return normalised

    @staticmethod
    def validate_supplier_type(supplier_type: str) -> str:
        normalised = (supplier_type or "any").strip().lower()
        if normalised not in SUPPORTED_SUPPLIER_TYPES:
            raise InvalidInputError(
                f"Unsupported supplier_type '{supplier_type}'.",
                remediation=f"Use one of: {', '.join(t.title() for t in SUPPORTED_SUPPLIER_TYPES)}.",
            )
        return normalised

    # -- search ----------------------------------------------------------- #
    async def search_suppliers(
        self, product_name: str, location: str = "India", supplier_type: str = "any"
    ) -> dict[str, Any]:
        """Return verified suppliers when an API is configured, else sourcing channels."""
        product_name = (product_name or "").strip()
        if len(product_name) < 2:
            raise InvalidInputError("Invalid product_name: provide at least 2 characters.")
        location = self.validate_location(location)
        supplier_type = self.validate_supplier_type(supplier_type)

        cache_key = f"suppliers::{product_name.lower()}::{location}::{supplier_type}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        if self.settings.supplier_api_key and self.settings.supplier_api_base_url:
            result = await self._search_via_api(product_name, location, supplier_type)
        else:
            result = self._sourcing_guidance(product_name, location, supplier_type)

        self._cache.set(cache_key, result)
        return result

    async def _search_via_api(self, product_name: str, location: str, supplier_type: str) -> dict[str, Any]:
        """Query a configured supplier data API and pass its verification status through."""
        base_url = (self.settings.supplier_api_base_url or "").rstrip("/")
        headers = {"Authorization": f"Bearer {self.settings.supplier_api_key}", "Accept": "application/json"}
        params = {"query": product_name, "location": location, "type": supplier_type}
        try:
            async with httpx.AsyncClient(timeout=self.settings.http_timeout_seconds) as client:
                response = await client.get(f"{base_url}/suppliers", params=params, headers=headers)
        except httpx.HTTPError as exc:
            logger.exception("Supplier API request failed")
            raise ServiceError(f"Supplier data unavailable: {type(exc).__name__}.") from exc

        if response.status_code == 429:
            raise RateLimitError("Supplier data provider rate limit exceeded.")
        if response.status_code >= 400:
            raise ServiceError(f"Supplier data unavailable (HTTP {response.status_code}).")

        payload = response.json()
        suppliers = [
            SupplierRecord(
                supplier_name=str(item.get("name", "Unknown")),
                location=str(item.get("location", location.title())),
                supplier_type=str(item.get("type", supplier_type.title())),
                estimated_price=item.get("price"),
                minimum_order_quantity=item.get("moq"),
                product_category=str(item.get("category", infer_category(product_name))),
                website_or_source=str(item.get("source", base_url)),
                contact_available=bool(item.get("contact_available", False)),
                sample_available=str(item.get("sample_available", "Unknown")),
                # Trust only what the provider itself asserts.
                verification_status=(
                    str(item.get("verification_status"))
                    if str(item.get("verification_status")) in VERIFICATION_STATUSES
                    else "Unverified"
                ),
                notes=item.get("notes"),
            ).model_dump()
            for item in (payload.get("suppliers") or [])
        ]
        return {
            "product_name": product_name,
            "location": location.title(),
            "supplier_type": supplier_type.title(),
            "supplier_data_available": bool(suppliers),
            "suppliers": suppliers,
            "sourcing_channels": [],
            "notice": "Supplier records come from the configured supplier data provider. Verify GSTIN and samples before paying.",
            "sourcing_checklist": SOURCING_CHECKLIST,
            **DataEnvelope(
                source="Configured supplier data provider",
                data_type=DataType.LIVE,
                confidence=Confidence.MEDIUM,
                notes="Verification status is passed through from the provider and is not independently audited.",
            ).as_dict(),
        }

    def _sourcing_guidance(self, product_name: str, location: str, supplier_type: str) -> dict[str, Any]:
        """No supplier API configured: return real public sourcing channels only."""
        category = infer_category(product_name)
        text = product_name.lower()
        channels = [
            channel for channel in SOURCING_CHANNELS
            if _location_matches(channel["location"], location)
            and _type_matches(channel["supplier_type"], supplier_type)
        ]
        channels.sort(key=lambda channel: -_keyword_relevance(channel, text))

        records = [
            SupplierRecord(
                supplier_name=channel["supplier_name"],
                location=channel["location"],
                supplier_type=channel["supplier_type"],
                estimated_price=None,
                minimum_order_quantity=None,
                product_category=channel["product_category"],
                website_or_source=channel["website_or_source"],
                contact_available=False,
                sample_available="Ask the individual seller directly",
                verification_status="Public Listing",
                notes=channel["notes"],
            ).model_dump()
            for channel in channels[:8]
        ]

        return {
            "product_name": product_name,
            "location": location.title(),
            "supplier_type": supplier_type.title(),
            "product_category": category,
            "supplier_data_available": False,
            "suppliers": [],
            "sourcing_channels": records,
            "notice": (
                "No supplier data provider is configured (SUPPLIER_API_KEY is unset), so no individual supplier "
                "records can be returned. Inventing supplier names, prices or MOQs would be misleading, so this "
                "tool instead lists real, publicly known sourcing channels you can verify yourself."
            ),
            "sourcing_checklist": SOURCING_CHECKLIST,
            "cost_guidance": {
                "note": "Indicative only - always collect at least three real quotations before committing.",
                "data_type": DataType.ESTIMATED.value,
                "typical_first_order_investment_inr": "5,000 - 20,000 for a beginner test order",
                "typical_moq_market_purchase": "12 - 100 units from a wholesale market or Udaan",
                "typical_moq_factory": "300 - 1,000 units direct from a manufacturer",
                "landed_cost_reminder": "Add GST, freight, packaging, FNSKU labelling and inward shipping to Amazon FC.",
            },
            **DataEnvelope(
                source="Curated list of public Indian sourcing channels",
                data_type=DataType.VERIFIED if not self.settings.is_demo else DataType.ESTIMATED,
                confidence=Confidence.MEDIUM,
                notes="Channel entries are public market areas and directories, not vetted supplier records.",
            ).as_dict(),
        }


SOURCING_CHECKLIST: list[str] = [
    "Ask for the GSTIN and verify it on the GST portal before paying anything.",
    "Buy a paid sample first - never place a bulk order on photos alone.",
    "Get the quotation in writing with unit price, GST, MOQ and delivery timeline.",
    "Confirm who pays freight and what the packaging spec is (Amazon needs poly-bag warnings).",
    "For branded-looking goods, ask for a brand authorisation letter or walk away.",
    "Pay through traceable bank transfer against an invoice - avoid full advance to unknown sellers.",
    "Check whether the category needs BIS, FSSAI or other certification before you commit.",
]


def _location_matches(channel_location: str, requested: str) -> bool:
    location = channel_location.lower()
    if requested == "india":
        return True
    if requested == "tamil nadu":
        return "tamil nadu" in location or "nationwide" in location
    if requested == "chennai":
        return "chennai" in location or "nationwide" in location
    if requested == "parrys":
        return "parrys" in location or "chennai" in location
    return True


def _type_matches(channel_type: str, requested: str) -> bool:
    if requested == "any":
        return True
    mapping = {
        "manufacturer": ("Manufacturing Cluster", "Government Directory", "B2B Directory"),
        "wholesaler": ("Wholesale Market Area", "Online Wholesaler", "B2B Directory"),
        "trader": ("Wholesale Market Area", "B2B Directory", "Online Wholesaler"),
    }
    return channel_type in mapping.get(requested, ())


def _keyword_relevance(channel: dict[str, Any], text: str) -> int:
    return sum(1 for keyword in channel.get("keywords", ()) if keyword in text)
