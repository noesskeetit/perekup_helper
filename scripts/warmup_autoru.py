"""Warm up Auto.ru session cookies via curl_cffi.

Opens a Chrome-impersonated session through the configured proxy,
visits Auto.ru to collect session cookies, and saves them for the
Auto.ru parser to reuse.

Playwright headless does NOT work — Yandex SmartWebSecurity blocks it.
curl_cffi with Chrome impersonation + proxy is the reliable approach.
"""

import json
import os
import sys
import time
from pathlib import Path

COOKIES_PATH = Path("storage/autoru_cookies.json")


def warmup():
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        print("ERROR: curl_cffi is required. Install: pip install curl_cffi")
        sys.exit(1)

    # Load proxy from .env if available
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    proxy_string = os.environ.get("PROXY_STRING", "")
    proxy_type = os.environ.get("PROXY_TYPE", "socks5")

    session = cffi_requests.Session(impersonate="chrome")
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8",
        "Referer": "https://auto.ru/",
    })

    if proxy_string:
        proxy_url = f"{proxy_type}://{proxy_string}"
        session.proxies = {"http": proxy_url, "https": proxy_url}
        print(f"Using proxy: {proxy_type}://***")
    else:
        print("WARNING: No PROXY_STRING set. Auto.ru may block direct requests.")

    # Step 1: Visit main page to initialize session
    print("  Visiting auto.ru main page...")
    try:
        resp = session.get("https://auto.ru/", timeout=20)
        print(f"    Status: {resp.status_code}, size: {len(resp.text)} bytes")
    except Exception as e:
        print(f"    Failed: {e}")
        return None
    time.sleep(3)

    # Step 2: Visit a search page to trigger full cookie set
    print("  Visiting Toyota search page...")
    try:
        resp = session.get(
            "https://auto.ru/cars/toyota/used/?geo_id=213&price_from=100000&price_to=3000000",
            timeout=120,
        )
        print(f"    Status: {resp.status_code}, size: {len(resp.text)} bytes")
        has_data = "mark_info" in resp.text
        print(f"    Has listing data: {has_data}")
        if not has_data:
            print("    WARNING: Page returned but no listing data found (possible captcha)")
    except Exception as e:
        print(f"    Failed: {e}")

    # Extract and save cookies
    cookies_dict = {}
    for cookie in session.cookies.jar:
        cookies_dict[cookie.name] = cookie.value

    if not cookies_dict:
        print("ERROR: No cookies collected!")
        return None

    COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    COOKIES_PATH.write_text(json.dumps(cookies_dict, indent=2), encoding="utf-8")

    key_names = ["autoru_sid", "suid", "_yasc", "yandexuid", "autoruuid"]
    found_keys = [k for k in key_names if k in cookies_dict]

    print(f"Saved {len(cookies_dict)} cookies to {COOKIES_PATH}")
    print(f"Key cookies: {found_keys}")
    print(f"All cookie names: {list(cookies_dict.keys())}")

    return cookies_dict


if __name__ == "__main__":
    warmup()
