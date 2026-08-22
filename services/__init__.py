"""Business logic services plus the shared primitives they all rely on.

This module holds the small, cross-cutting building blocks used by every
service: data-provenance envelopes, service level exceptions, a tiny TTL cache,
deterministic randomness for demo mode, and the product opportunity scoring
model.  Keeping them here avoids inventing extra packages for a handful of
shared types.
"""

from __future__ import annotations

import hashlib
import random
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

from pydantic import BaseModel, Field

__all__ = [
    "DataType",
    "Confidence",
    "DataEnvelope",
    "ServiceError",
    "ProviderNotConfiguredError",
    "InsufficientDataError",
    "RateLimitError",
    "InvalidInputError",
    "TTLCache",
    "deterministic_rng",
    "stable_hash",
    "utc_now_iso",
    "OpportunityScores",
    "OpportunityResult",
    "score_opportunity",
    "recommendation_for_score",
    "SCORE_WEIGHTS",
]


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #
class DataType(str, Enum):
    """How trustworthy a piece of data is."""

    LIVE = "Live"
    VERIFIED = "Verified"
    ESTIMATED = "Estimated"
    HISTORICAL = "Historical"
    DEMO = "Demo"


class Confidence(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


def utc_now_iso() -> str:
    """Current UTC timestamp as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DataEnvelope(BaseModel):
    """Provenance metadata attached to every meaningful result.

    Nothing in this project is ever returned without one of these, so a reader
    can always tell live marketplace data apart from an estimate or demo value.
    """

    source: str
    data_type: DataType
    confidence: Confidence
    last_updated: str = Field(default_factory=utc_now_iso)
    notes: str | None = None

    @classmethod
    def demo(cls, source: str = "Local Demo Provider", notes: str | None = None) -> "DataEnvelope":
        return cls(
            source=source,
            data_type=DataType.DEMO,
            confidence=Confidence.LOW,
            notes=notes or "Deterministic demo data. Not real Amazon marketplace data.",
        )

    @classmethod
    def estimated(
        cls,
        source: str,
        confidence: Confidence = Confidence.MEDIUM,
        notes: str | None = None,
    ) -> "DataEnvelope":
        return cls(source=source, data_type=DataType.ESTIMATED, confidence=confidence, notes=notes)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "data_type": self.data_type.value,
            "confidence": self.confidence.value,
            "last_updated": self.last_updated,
            "notes": self.notes,
        }


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class ServiceError(Exception):
    """Base class for user-safe service errors.

    The message of a ``ServiceError`` is safe to show to an MCP client; raw
    exceptions from providers are never surfaced.
    """

    code = "service_error"
    remediation: str | None = None

    def __init__(self, message: str, *, remediation: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if remediation:
            self.remediation = remediation

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "remediation": self.remediation,
        }


class ProviderNotConfiguredError(ServiceError):
    code = "provider_not_configured"
    remediation = (
        "Configure PRODUCT_DATA_PROVIDER and PRODUCT_DATA_API_KEY in .env, "
        "or set DEMO_MODE=true to explore the tools with clearly labelled demo data."
    )


class InsufficientDataError(ServiceError):
    code = "insufficient_data"
    remediation = "Try a broader or more common product keyword."


class RateLimitError(ServiceError):
    code = "rate_limit_exceeded"
    remediation = "Wait for the provider rate-limit window to reset and retry."


class InvalidInputError(ServiceError):
    code = "invalid_input"
    remediation = "Correct the highlighted input value and call the tool again."


# --------------------------------------------------------------------------- #
# Cache + determinism helpers
# --------------------------------------------------------------------------- #
class TTLCache:
    """Minimal in-process time-to-live cache."""

    def __init__(self, ttl_seconds: int = 900, enabled: bool = True) -> None:
        self.ttl_seconds = ttl_seconds
        self.enabled = enabled
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        if not self.enabled:
            return None
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at < time.monotonic():
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        if not self.enabled:
            return
        self._store[key] = (time.monotonic() + self.ttl_seconds, value)

    async def get_or_set(self, key: str, factory: Callable[[], Any]) -> Any:
        cached = self.get(key)
        if cached is not None:
            return cached
        value = await factory()
        self.set(key, value)
        return value

    def clear(self) -> None:
        self._store.clear()


def stable_hash(*parts: str) -> int:
    """A stable, cross-process hash used to make demo data deterministic."""
    joined = "||".join(p.strip().lower() for p in parts if p)
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    return int(digest[:12], 16)


def deterministic_rng(*parts: str) -> random.Random:
    """Return a seeded RNG so the same query always yields the same demo data."""
    return random.Random(stable_hash(*parts))


# --------------------------------------------------------------------------- #
# Opportunity scoring
# --------------------------------------------------------------------------- #
SCORE_WEIGHTS: dict[str, float] = {
    "demand": 0.25,
    "profitability": 0.25,
    "competition": 0.20,
    "return_risk": 0.10,
    "sourcing_ease": 0.10,
    "beginner_friendliness": 0.10,
}


class OpportunityScores(BaseModel):
    """The six weighted sub-scores, each 0-100."""

    demand: float = Field(ge=0, le=100)
    profitability: float = Field(ge=0, le=100)
    competition: float = Field(ge=0, le=100)
    return_risk: float = Field(ge=0, le=100)
    sourcing_ease: float = Field(ge=0, le=100)
    beginner_friendliness: float = Field(ge=0, le=100)


class OpportunityResult(BaseModel):
    overall_opportunity_score: int
    recommendation: str
    scores: OpportunityScores
    weights: dict[str, float] = Field(default_factory=lambda: dict(SCORE_WEIGHTS))
    penalties: list[str] = Field(default_factory=list)


def recommendation_for_score(score: float) -> str:
    """Map a 0-100 opportunity score onto the recommendation bands."""
    if score >= 80:
        return "Strong Opportunity"
    if score >= 65:
        return "Good Opportunity"
    if score >= 50:
        return "Moderate Opportunity"
    if score >= 30:
        return "High Risk"
    return "Avoid"


def score_opportunity(
    scores: OpportunityScores,
    penalties: list[str] | None = None,
    penalty_points: float = 0.0,
) -> OpportunityResult:
    """Combine the weighted sub-scores into an overall 0-100 opportunity score."""
    values = scores.model_dump()
    total = sum(values[key] * weight for key, weight in SCORE_WEIGHTS.items())
    total = max(0.0, min(100.0, total - penalty_points))
    return OpportunityResult(
        overall_opportunity_score=int(round(total)),
        recommendation=recommendation_for_score(total),
        scores=scores,
        penalties=penalties or [],
    )
