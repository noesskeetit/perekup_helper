from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./perekup.db"
    app_title: str = "Perekup Helper API"
    app_description: str = (
        "AI-агрегатор для перекупов: "
        "парсинг авто-объявлений, анализ цен ниже рынка, AI-категоризация"
    )
    app_version: str = "0.1.0"


settings = Settings()
