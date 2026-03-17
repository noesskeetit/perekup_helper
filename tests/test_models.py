"""Tests for database models and upsert logic."""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.avito_parser.models import Base, CarAd, upsert_car_ad


def _make_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    return factory()


class TestUpsertCarAd:
    def test_insert_new(self):
        session = _make_session()
        data = {
            "external_id": "12345",
            "url": "https://www.avito.ru/test_12345",
            "title": "Toyota Camry",
            "brand": "Toyota",
            "model": "Camry",
            "year": 2020,
            "price": 1500000,
        }
        ad = upsert_car_ad(session, data)
        session.commit()

        assert ad.id is not None
        assert ad.external_id == "12345"
        assert ad.brand == "Toyota"
        assert ad.price == 1500000

    def test_update_existing(self):
        session = _make_session()
        data = {
            "external_id": "12345",
            "url": "https://www.avito.ru/test_12345",
            "title": "Toyota Camry",
            "price": 1500000,
        }
        upsert_car_ad(session, data)
        session.commit()

        updated_data = {
            "external_id": "12345",
            "price": 1400000,
            "mileage_km": 50000,
        }
        ad = upsert_car_ad(session, updated_data)
        session.commit()

        assert ad.price == 1400000
        assert ad.mileage_km == 50000
        assert ad.title == "Toyota Camry"  # preserved from first insert

        # Should still be one record
        count = session.query(CarAd).count()
        assert count == 1

    def test_deduplication(self):
        session = _make_session()
        for i in range(3):
            upsert_car_ad(session, {
                "external_id": "same_id",
                "url": "https://www.avito.ru/test_same_id",
                "price": 1000000 + i * 100000,
            })
        session.commit()

        count = session.query(CarAd).count()
        assert count == 1

        ad = session.query(CarAd).first()
        assert ad.price == 1200000  # last update

    def test_multiple_distinct_ads(self):
        session = _make_session()
        for i in range(5):
            upsert_car_ad(session, {
                "external_id": f"ad_{i}",
                "url": f"https://www.avito.ru/test_{i}",
                "price": 1000000 * (i + 1),
            })
        session.commit()

        count = session.query(CarAd).count()
        assert count == 5

    def test_missing_external_id_raises(self):
        session = _make_session()
        import pytest

        with pytest.raises(ValueError, match="external_id"):
            upsert_car_ad(session, {"url": "test", "price": 100})
