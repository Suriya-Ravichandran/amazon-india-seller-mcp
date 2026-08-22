"""Centralised application settings for the Amazon India Product Research MCP.

All configuration is read from environment variables (optionally via a ``.env``
file).  Nothing that looks like a credential is ever hardcoded here.

Marketplace fee information is *configuration*, not code: the defaults below are
clearly labelled ``Estimated`` and can be overridden completely by pointing
``AMAZON_FEE_CONFIG_PATH`` at a JSON file containing a verified fee schedule
exported from Seller Central.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DataTypeLiteral = Literal["Live", "Verified", "Estimated", "Historical", "Demo"]


# --------------------------------------------------------------------------- #
# Fee schedule models
# --------------------------------------------------------------------------- #
class WeightSlab(BaseModel):
    """A fulfilment fee slab expressed in grams."""

    up_to_grams: int = Field(gt=0, description="Upper bound of the slab, inclusive.")
    fee: float = Field(ge=0, description="Flat fee in INR for this slab.")


class FeeSchedule(BaseModel):
    """Amazon India fee schedule.

    The shipped defaults are *approximations* used so the MCP is usable out of
    the box.  They are labelled ``Estimated`` and must be replaced with the
    seller's actual rate card before any real launch decision is taken.
    """

    data_type: DataTypeLiteral = "Estimated"
    source: str = "Bundled default fee schedule (approximate, not verified)"
    effective_date: date = date(2025, 1, 1)
    currency: str = "INR"

    # Referral fee as a fraction of the item selling price, keyed by category.
    referral_fee_rates: dict[str, float] = Field(
        default_factory=lambda: {
            "Home & Kitchen": 0.085,
            "Kitchen": 0.085,
            "Home Improvement": 0.09,
            "Beauty": 0.085,
            "Health & Personal Care": 0.07,
            "Baby": 0.07,
            "Office Products": 0.09,
            "Stationery": 0.09,
            "Sports & Fitness": 0.09,
            "Pet Supplies": 0.09,
            "Toys": 0.09,
            "Automotive Accessories": 0.09,
            "Electronics Accessories": 0.09,
            "Mobile Accessories": 0.09,
            "Apparel": 0.14,
            "Jewellery": 0.12,
            "Grocery": 0.06,
            "default": 0.09,
        }
    )

    # Closing fee slabs by selling price (INR).
    closing_fee_slabs: list[dict[str, float]] = Field(
        default_factory=lambda: [
            {"up_to_price": 250.0, "fba": 20.0, "easy_ship": 8.0, "self_ship": 5.0},
            {"up_to_price": 500.0, "fba": 26.0, "easy_ship": 14.0, "self_ship": 10.0},
            {"up_to_price": 1000.0, "fba": 40.0, "easy_ship": 26.0, "self_ship": 20.0},
            {"up_to_price": 1_000_000.0, "fba": 55.0, "easy_ship": 40.0, "self_ship": 30.0},
        ]
    )

    # Fulfilment fee slabs (local / regional delivery, standard size).
    fba_weight_slabs: list[WeightSlab] = Field(
        default_factory=lambda: [
            WeightSlab(up_to_grams=250, fee=32.0),
            WeightSlab(up_to_grams=500, fee=44.0),
            WeightSlab(up_to_grams=1000, fee=62.0),
            WeightSlab(up_to_grams=5000, fee=95.0),
            WeightSlab(up_to_grams=100_000, fee=145.0),
        ]
    )
    easy_ship_weight_slabs: list[WeightSlab] = Field(
        default_factory=lambda: [
            WeightSlab(up_to_grams=500, fee=58.0),
            WeightSlab(up_to_grams=1000, fee=76.0),
            WeightSlab(up_to_grams=5000, fee=110.0),
            WeightSlab(up_to_grams=100_000, fee=165.0),
        ]
    )
    self_ship_weight_slabs: list[WeightSlab] = Field(
        default_factory=lambda: [
            WeightSlab(up_to_grams=500, fee=45.0),
            WeightSlab(up_to_grams=1000, fee=65.0),
            WeightSlab(up_to_grams=5000, fee=105.0),
            WeightSlab(up_to_grams=100_000, fee=160.0),
        ]
    )

    # Storage / misc.
    monthly_storage_fee_per_unit: float = 1.5
    gst_rate_on_fees: float = 0.18
    payment_gateway_rate: float = 0.0

    def referral_rate_for(self, category: str | None) -> tuple[float, str]:
        """Return ``(rate, matched_category)`` for a category name."""
        if category:
            for name, rate in self.referral_fee_rates.items():
                if name.lower() == category.strip().lower():
                    return rate, name
            for name, rate in self.referral_fee_rates.items():
                if name != "default" and name.lower() in category.strip().lower():
                    return rate, name
        return self.referral_fee_rates.get("default", 0.09), "default"

    def closing_fee_for(self, price: float, fulfilment_key: str) -> float:
        for slab in sorted(self.closing_fee_slabs, key=lambda s: s["up_to_price"]):
            if price <= slab["up_to_price"]:
                return float(slab.get(fulfilment_key, slab.get("self_ship", 0.0)))
        return 0.0

    def fulfilment_fee_for(self, weight_grams: float, fulfilment_key: str) -> float:
        slabs = {
            "fba": self.fba_weight_slabs,
            "easy_ship": self.easy_ship_weight_slabs,
            "self_ship": self.self_ship_weight_slabs,
        }[fulfilment_key]
        for slab in sorted(slabs, key=lambda s: s.up_to_grams):
            if weight_grams <= slab.up_to_grams:
                return slab.fee
        return slabs[-1].fee


# --------------------------------------------------------------------------- #
# Beginner product filter thresholds
# --------------------------------------------------------------------------- #
class BeginnerCriteria(BaseModel):
    """Thresholds describing the beginner-friendly Amazon India seller profile."""

    min_investment_inr: float = 5_000
    max_investment_inr: float = 20_000
    min_selling_price_inr: float = 199
    max_selling_price_inr: float = 699
    max_weight_grams: float = 500
    min_profit_margin: float = 0.30
    max_return_rate: float = 0.08

    penalised_traits: list[str] = Field(
        default_factory=lambda: [
            "branded",
            "counterfeit_risk",
            "fragile",
            "high_return",
            "complex_electronics",
            "battery",
            "hazardous",
            "perishable",
            "seasonal",
            "apparel_sizing",
            "heavy",
            "strong_brand_dominance",
        ]
    )


# --------------------------------------------------------------------------- #
# Application settings
# --------------------------------------------------------------------------- #
class Settings(BaseSettings):
    """Environment-driven application settings."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # General
    app_env: str = "development"
    debug: bool = False
    log_level: str = "INFO"

    # Database
    database_url: str = f"sqlite:///{(PROJECT_ROOT / 'amazon_product_mcp.db').as_posix()}"
    db_echo: bool = False
    persist_research: bool = True

    # Amazon credentials (SP-API / PA-API) — never hardcode values here.
    amazon_api_key: str | None = None
    amazon_api_secret: str | None = None
    amazon_marketplace: str = "amazon.in"

    # Third party product data provider
    product_data_provider: str = "demo"
    product_data_api_key: str | None = None
    product_data_base_url: str | None = None

    # Trends
    google_trends_enabled: bool = False

    # Suppliers
    supplier_api_key: str | None = None
    supplier_api_base_url: str | None = None

    # Behaviour
    demo_mode: bool = True
    cache_enabled: bool = True
    cache_ttl_seconds: int = 900
    http_timeout_seconds: float = 20.0

    # Fee configuration
    amazon_fee_config_path: str | None = None

    @field_validator("product_data_provider")
    @classmethod
    def _normalise_provider(cls, value: str) -> str:
        return value.strip().lower() or "demo"

    @property
    def is_demo(self) -> bool:
        """Demo mode is on when explicitly requested or when no provider is configured."""
        if self.demo_mode:
            return True
        return self.product_data_provider == "demo" or not self.product_data_api_key

    @property
    def beginner_criteria(self) -> BeginnerCriteria:
        return BeginnerCriteria()

    @property
    def fee_schedule(self) -> FeeSchedule:
        """Load the fee schedule, preferring an operator supplied JSON file."""
        if self.amazon_fee_config_path:
            path = Path(self.amazon_fee_config_path)
            if path.is_file():
                try:
                    return FeeSchedule(**json.loads(path.read_text(encoding="utf-8")))
                except Exception:  # noqa: BLE001 - configuration must never crash startup
                    logger.exception("Invalid fee config at %s; using bundled defaults", path)
            else:
                logger.warning("AMAZON_FEE_CONFIG_PATH %s not found; using defaults", path)
        return FeeSchedule()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()


def configure_logging(settings: Settings | None = None) -> None:
    """Configure logging to stderr so stdout stays reserved for MCP stdio traffic."""
    import sys

    settings = settings or get_settings()
    level = logging.DEBUG if settings.debug else getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        force=True,
    )
