from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
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

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    brand: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    mileage: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    market_price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    price_diff_pct: Mapped[Optional[float]] = mapped_column(Numeric(5, 2), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    photos: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String), nullable=True)
    raw_data: Mapped[Optional[Dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    analysis: Mapped[Optional[ListingAnalysis]] = relationship(back_populates="listing", uselist=False)


class ListingAnalysis(Base):
    __tablename__ = "listing_analysis"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("listings.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    category: Mapped[AnalysisCategory] = mapped_column(String(30), nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)
    ai_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    flags: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    listing: Mapped[Listing] = relationship(back_populates="analysis")
