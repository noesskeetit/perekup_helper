import re
from loguru import logger


def mask_sensitive_data(config_str: str) -> str:
    masked = config_str

    # Прокси (поддержка http/https и более гибких логинов/паролей)
    masked = re.sub(
        r"(https?:\/\/)?([^:@\s]+):([^@\s]+)@([^:\s]+):(\d+)",
        lambda m: f"{m.group(2)}:***@{m.group(4)}:{m.group(5)}",
        masked,
    )

    # Telegram token — полностью скрываем
    masked = re.sub(
        r"(tg_token[\"']?\s*[:=]\s*[\"'])([^\"']+)([\"'])",
        lambda m: f"{m.group(1)}***{m.group(3)}",
        masked,
    )

    # Telegram chat_id
    masked = re.sub(
        r"(tg_chat_id[\"']?\s*[:=]\s*)(\[?[^\]]*\]?)",
        lambda m: f"{m.group(1)}***",
        masked,
    )

    # proxy_change_url
    masked = re.sub(
        r"(proxy_change_url[\"']?\s*[:=]\s*[\"'])([^\"']+)([\"'])",
        lambda m: f"{m.group(1)}{_mask_url(m.group(2))}{m.group(3)}",
        masked,
    )

    # Общая маска
    masked = re.sub(
        r"((?:password|pass|token|api[_-]?key|secret|auth|session)[\"']?\s*[:=]\s*[\"'])([^\"']+)([\"'])",
        lambda m: f"{m.group(1)}***{m.group(3)}",
        masked,
        flags=re.IGNORECASE,
    )

    return masked


def _mask_url(url: str) -> str:
    match = re.search(r"(https?://)([^/]+)/?", url)
    if match:
        return f"{match.group(1)}{match.group(2)}/***"
    return "***"


def log_config(config):
    safe_config_str = mask_sensitive_data(str(config))
    logger.info(f"Запуск AviPars с настройками:\n{safe_config_str}")