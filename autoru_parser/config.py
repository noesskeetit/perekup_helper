from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    db_url: str = "sqlite:///autoru_ads.db"
    request_delay_min: float = 2.0
    request_delay_max: float = 5.0
    max_pages: int = 10
    autoru_base_url: str = "https://auto.ru"

    model_config = {"env_prefix": "AUTORU_"}


settings = Settings()
