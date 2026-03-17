from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Integer, String, Text

from app.database import Base


class Listing(Base):
    __tablename__ = "listings"

    id = Column(Integer, primary_key=True, index=True)
    brand = Column(String, index=True, nullable=False)
    model = Column(String, index=True, nullable=False)
    year = Column(Integer, index=True, nullable=False)
    price = Column(Float, nullable=False)
    mileage = Column(Integer, nullable=False)
    market_price = Column(Float, nullable=True)
    price_diff = Column(Float, nullable=True)
    market_diff_pct = Column(Float, nullable=True)
    score = Column(Float, nullable=True)
    category = Column(String, nullable=True)
    ai_analysis = Column(Text, nullable=True)
    source_url = Column(String, nullable=True)
    image_url = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
