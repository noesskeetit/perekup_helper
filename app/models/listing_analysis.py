from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class AnalysisCategory(str, enum.Enum):
    CLEAN = "clean"
    DAMAGED_BODY = "damaged_body"
    BAD_DOCS = "bad_docs"
    DEBTOR = "debtor"
    COMPLEX_BUT_PROFITABLE = "complex_but_profitable"


class ListingAnalysis(Base):
    __tablename__ = "listing_analysis"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("listings.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    category: Mapped[AnalysisCategory] = mapped_column(
        String(30), nullable=False
    )
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)
    ai_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    flags: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    listing: Mapped[Listing] = relationship(back_populates="analysis")


from app.models.listing import Listing  # noqa: E402, F401
