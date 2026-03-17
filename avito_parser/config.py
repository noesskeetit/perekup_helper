from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    db_url: str = "sqlite:///avito_ads.db"
    request_delay_min: float = 2.0
    request_delay_max: float = 5.0
    max_pages: int = 10
    update_interval_minutes: int = 30
    avito_base_url: str = "https://www.avito.ru"

    model_config = {"env_prefix": "AVITO_"}


settings = Settings()
