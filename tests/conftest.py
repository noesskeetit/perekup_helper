from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import Listing

TEST_DB_URL = "sqlite:///./test.db"
engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def sample_listings(db):
    listings = [
        Listing(
            brand="Toyota",
            model="Camry",
            year=2020,
            price=2_500_000,
            mileage=45_000,
            market_price=2_800_000,
            price_diff=-300_000,
            market_diff_pct=-10.71,
            score=8.5,
            category="clean",
            ai_analysis="Отличное состояние, один владелец, полный сервис у дилера.",
            source_url="https://auto.ru/cars/toyota/camry/1",
            created_at=datetime(2024, 1, 15),
            updated_at=datetime(2024, 1, 15),
        ),
        Listing(
            brand="BMW",
            model="X5",
            year=2019,
            price=3_200_000,
            mileage=80_000,
            market_price=3_500_000,
            price_diff=-300_000,
            market_diff_pct=-8.57,
            score=7.2,
            category="medium",
            ai_analysis="Два владельца, мелкие ДТП по кузову. Двигатель в норме.",
            source_url="https://auto.ru/cars/bmw/x5/2",
            created_at=datetime(2024, 2, 10),
            updated_at=datetime(2024, 2, 10),
        ),
        Listing(
            brand="Toyota",
            model="RAV4",
            year=2021,
            price=2_900_000,
            mileage=30_000,
            market_price=3_000_000,
            price_diff=-100_000,
            market_diff_pct=-3.33,
            score=9.0,
            category="clean",
            ai_analysis="Идеальное состояние, минимальный пробег.",
            source_url="https://auto.ru/cars/toyota/rav4/3",
            created_at=datetime(2024, 3, 5),
            updated_at=datetime(2024, 3, 5),
        ),
        Listing(
            brand="Kia",
            model="Sportage",
            year=2018,
            price=1_600_000,
            mileage=120_000,
            market_price=1_500_000,
            price_diff=100_000,
            market_diff_pct=6.67,
            score=4.5,
            category="risky",
            ai_analysis="Завышенная цена, высокий пробег, следы некачественного ремонта.",
            source_url="https://auto.ru/cars/kia/sportage/4",
            created_at=datetime(2024, 4, 1),
            updated_at=datetime(2024, 4, 1),
        ),
        Listing(
            brand="Hyundai",
            model="Tucson",
            year=2022,
            price=2_700_000,
            mileage=15_000,
            market_price=2_900_000,
            price_diff=-200_000,
            market_diff_pct=-6.90,
            score=8.8,
            category="clean",
            ai_analysis="Практически новый автомобиль, гарантия дилера.",
            source_url="https://auto.ru/cars/hyundai/tucson/5",
            created_at=datetime(2024, 5, 20),
            updated_at=datetime(2024, 5, 20),
        ),
    ]
    db.add_all(listings)
    db.commit()
    for listing in listings:
        db.refresh(listing)
    return listings
