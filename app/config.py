from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/perekup"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    parse_interval_minutes: int = 30
    openrouter_api_key: str = ""
    openrouter_model: str = "qwen/qwen3.6-plus:free"
    cloudru_fm_api_key: str = ""
    cloudru_fm_url: str = "https://foundation-models.api.cloud.ru/v1/chat/completions"
    cloudru_ocr_model: str = "deepseek-ai/DeepSeek-OCR-2"
    cloudru_text_model: str = "zai-org/GLM-4.7"
    ai_provider: str = "cloudru"  # "cloudru" or "openrouter"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
