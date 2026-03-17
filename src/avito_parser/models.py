from datetime import UTC, datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


class CarAd(Base):
    __tablename__ = "car_ads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    external_id = Column(String(64), nullable=False, index=True)
    url = Column(String(512), nullable=False)
    title = Column(String(512))

    # Car parameters
    brand = Column(String(128))
    model = Column(String(128))
    year = Column(Integer)
    mileage_km = Column(Integer)
    engine_type = Column(String(64))
    engine_volume = Column(Float)
    engine_power_hp = Column(Integer)
    transmission = Column(String(64))
    drive_type = Column(String(64))
    body_type = Column(String(64))
    color = Column(String(64))
    steering_wheel = Column(String(32))

    # Price
    price = Column(Integer)
    market_price = Column(Integer)
    price_deviation_pct = Column(Float)

    # Content
    description = Column(Text)
    photo_urls = Column(Text)  # JSON array
    vin = Column(String(32))

    # Location
    location = Column(String(256))
    seller_name = Column(String(256))

    # Metadata
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))
    parsed_at = Column(DateTime, default=lambda: datetime.now(UTC))

    __table_args__ = (UniqueConstraint("external_id", name="uq_external_id"),)

    def __repr__(self) -> str:
        return f"<CarAd {self.external_id} {self.brand} {self.model} {self.year} {self.price}>"


def get_engine():
    return create_engine(settings.db_url, echo=False)


def get_session_factory():
    engine = get_engine()
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def upsert_car_ad(session: Session, data: dict) -> CarAd:
    """Insert or update a car ad by external_id."""
    external_id = data.get("external_id")
    if not external_id:
        raise ValueError("external_id is required")

    existing = session.query(CarAd).filter_by(external_id=external_id).first()
    if existing:
        for key, value in data.items():
            if value is not None:
                setattr(existing, key, value)
        existing.updated_at = datetime.now(UTC)
        session.flush()
        return existing

    ad = CarAd(**data)
    session.add(ad)
    session.flush()
    return ad
