from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Proxy:
    proxy_string: str
    change_ip_link: str


@dataclass
class ProxySplit:
    ip_port: str
    login: str
    password: str
    change_ip_link: str


@dataclass
class AvitoConfig:
    urls: list[str]
    proxy_string: str | None = None
    proxy_change_url: str | None = None
    keys_word_white_list: list[str] = field(default_factory=list)
    keys_word_black_list: list[str] = field(default_factory=list)
    seller_black_list: list[str] = field(default_factory=list)
    count: int = 1
    tg_token: str | None = None
    tg_chat_id: list[str] = None
    vk_token: str | None = None
    vk_user_id: list[str] = None
    max_price: int = 999_999_999
    min_price: int = 0
    geo: str | None = None
    max_age: int = 24 * 60 * 60
    debug_mode: int = 0
    pause_general: int = 60
    pause_between_links: int = 5
    max_count_of_retry: int = 5
    ignore_reserv: bool = True
    ignore_promotion: bool = False
    one_time_start: bool = False
    one_file_for_link: bool = False
    parse_views: bool = False
    save_xlsx: bool = True
    use_webdriver: bool = True
    use_bypass_api: bool = False
    cookies_api_key: str = None
    output_dir: Path = Path("result")
    use_own_cookies: bool = False
    parse_phone: bool = False
    proxy_notifier: str = None

