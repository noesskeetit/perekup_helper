from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class AnalysisCategory(str, enum.Enum):
    CLEAN = "clean"
    DAMAGED_BODY = "damaged_body"
    BAD_DOCS = "bad_docs"
    DEBTOR = "debtor"
    COMPLEX_BUT_PROFITABLE = "complex_but_profitable"


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_source_external_id"),
        Index("ix_listings_not_dup_diff", "is_duplicate", "price_diff_pct", postgresql_where="is_duplicate = false"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    brand: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    mileage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    market_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_diff_pct: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    photos: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    raw_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    vin: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    engine_type: Mapped[str | None] = mapped_column(String(50), nullable=True)  # бензин, дизель, гибрид, электро
    engine_volume: Mapped[float | None] = mapped_column(Float, nullable=True)  # литры
    power_hp: Mapped[int | None] = mapped_column(Integer, nullable=True)  # л.с.
    transmission: Mapped[str | None] = mapped_column(String(50), nullable=True)  # МКПП, АКПП, вариатор, робот
    drive_type: Mapped[str | None] = mapped_column(String(50), nullable=True)  # передний, задний, полный
    body_type: Mapped[str | None] = mapped_column(String(50), nullable=True)  # седан, кроссовер, хэтчбек
    color: Mapped[str | None] = mapped_column(String(50), nullable=True)
    owners_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    steering_wheel: Mapped[str | None] = mapped_column(String(20), nullable=True)
    condition: Mapped[str | None] = mapped_column(String(50), nullable=True)
    generation: Mapped[str | None] = mapped_column(String(100), nullable=True)
    modification: Mapped[str | None] = mapped_column(String(200), nullable=True)
    seller_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    seller_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    region: Mapped[str | None] = mapped_column(String(100), nullable=True)
    listing_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_dealer: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    pts_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    customs_cleared: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    photo_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    is_duplicate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false", index=True)
    canonical_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    analysis: Mapped[ListingAnalysis | None] = relationship(back_populates="listing", uselist=False)


class ListingAnalysis(Base):
    __tablename__ = "listing_analysis"
    __table_args__ = (
        Index("ix_analysis_category", "category"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    listing_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("listings.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    category: Mapped[AnalysisCategory] = mapped_column(String(30), nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    flags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    listing: Mapped[Listing] = relationship(back_populates="analysis")
