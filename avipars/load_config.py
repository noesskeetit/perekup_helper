import os
import tomllib
from pathlib import Path

import tomli_w
from dto import AvitoConfig


def load_avito_config(path: str = "config.toml") -> AvitoConfig:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    config = AvitoConfig(**data["avito"])

    # Override secrets from environment variables if set
    if os.environ.get("COOKIES_API_KEY"):
        config.cookies_api_key = os.environ["COOKIES_API_KEY"]
    if os.environ.get("PROXY_STRING"):
        config.proxy_string = os.environ["PROXY_STRING"]
    if os.environ.get("PROXY_CHANGE_URL"):
        config.proxy_change_url = os.environ["PROXY_CHANGE_URL"]

    return config


def save_avito_config(config: dict):
    with Path("config.toml").open("wb") as f:
        tomli_w.dump(config, f)
