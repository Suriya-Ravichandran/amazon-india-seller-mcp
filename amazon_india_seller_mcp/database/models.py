"""SQLAlchemy models and session handling for stored research history.

The models are deliberately uniform: every research artefact records what was
researched, on which marketplace, the full payload as JSON, and — critically —
where the data came from and how much it can be trusted.

Works on SQLite (development default) and PostgreSQL (JSON columns are mapped
to ``JSONB`` on PostgreSQL through a dialect variant).
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy import DateTime, Float, Index, Integer, String, Text, create_engine, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import JSON

from amazon_india_seller_mcp.config.settings import get_settings

logger = logging.getLogger(__name__)

# JSON on SQLite, JSONB on PostgreSQL.
JSONType = JSON().with_variant(JSONB, "postgresql")


class Base(DeclarativeBase):
    """Declarative base for all models."""


class ResearchRecordMixin:
    """Columns shared by every stored research artefact."""

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    marketplace: Mapped[str] = mapped_column(String(64), nullable=False, default="amazon.in")
    research_data: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    data_source: Mapped[str] = mapped_column(String(128), nullable=False, default="unknown")
    data_type: Mapped[str] = mapped_column(String(32), nullable=False, default="Demo")
    confidence: Mapped[str] = mapped_column(String(32), nullable=False, default="Low")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "product_name": self.product_name,
            "marketplace": self.marketplace,
            "research_data": self.research_data,
            "data_source": self.data_source,
            "data_type": self.data_type,
            "confidence": self.confidence,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ProductResearch(ResearchRecordMixin, Base):
    """A full product research run for a single product idea."""

    __tablename__ = "product_research"

    opportunity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    recommendation: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (Index("ix_product_research_name_market", "product_name", "marketplace"),)


class DemandAnalysis(ResearchRecordMixin, Base):
    """Demand / trend analysis for a product idea."""

    __tablename__ = "demand_analysis"

    demand_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    demand_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    trend_direction: Mapped[str | None] = mapped_column(String(32), nullable=True)
    seasonality_risk: Mapped[str | None] = mapped_column(String(32), nullable=True)


class CompetitionAnalysis(ResearchRecordMixin, Base):
    """Competitive landscape analysis for a keyword."""

    __tablename__ = "competition_analysis"

    keyword: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    competition_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    competitors_analysed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    average_price: Mapped[float | None] = mapped_column(Float, nullable=True)


class ProfitCalculation(ResearchRecordMixin, Base):
    """A single profitability calculation."""

    __tablename__ = "profit_calculation"

    selling_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimated_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    profit_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    roi: Mapped[float | None] = mapped_column(Float, nullable=True)
    fulfillment_method: Mapped[str | None] = mapped_column(String(32), nullable=True)


class SupplierResearch(ResearchRecordMixin, Base):
    """A supplier / sourcing research run."""

    __tablename__ = "supplier_research"

    location: Mapped[str | None] = mapped_column(String(128), nullable=True)
    supplier_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    suppliers_found: Mapped[int | None] = mapped_column(Integer, nullable=True)


# --------------------------------------------------------------------------- #
# Engine / session management
# --------------------------------------------------------------------------- #
_engine = None
_SessionFactory: sessionmaker[Session] | None = None


def get_engine():
    """Create (once) and return the SQLAlchemy engine for ``DATABASE_URL``."""
    global _engine, _SessionFactory
    if _engine is None:
        settings = get_settings()
        url = settings.database_url
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, echo=settings.db_echo, future=True, connect_args=connect_args)
        _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
        logger.debug("Database engine initialised for %s", url.split("@")[-1])
    return _engine


def init_db() -> None:
    """Create all tables if they do not exist yet."""
    Base.metadata.create_all(bind=get_engine())


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session scope."""
    get_engine()
    assert _SessionFactory is not None
    session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine() -> None:
    """Drop the cached engine (used by tests that swap DATABASE_URL)."""
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None


def save_record(model: type[Base], **fields: Any) -> int | None:
    """Persist one research record, returning its primary key.

    Persistence is best-effort: a database problem must never break an MCP tool
    call, so failures are logged and swallowed.
    """
    settings = get_settings()
    if not settings.persist_research:
        return None
    try:
        init_db()
        with session_scope() as session:
            record = model(**fields)  # type: ignore[call-arg]
            session.add(record)
            session.flush()
            return int(record.id)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - history storage is non critical
        logger.exception("Failed to persist %s record", getattr(model, "__tablename__", model))
        return None


def recent_records(model: type[Base], product_name: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
    """Return the most recent stored records for a model, newest first."""
    try:
        init_db()
        with session_scope() as session:
            query = session.query(model)
            if product_name:
                query = query.filter(model.product_name.ilike(f"%{product_name}%"))  # type: ignore[attr-defined]
            rows = query.order_by(model.created_at.desc()).limit(limit).all()  # type: ignore[attr-defined]
            return [row.to_dict() for row in rows]
    except Exception:  # noqa: BLE001
        logger.exception("Failed to read research history")
        return []
