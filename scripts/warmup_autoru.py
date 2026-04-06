"""Warm up Auto.ru session cookies via Playwright.

Opens a real browser, navigates Auto.ru, collects cookies,
saves them for the Auto.ru parser to use with curl_cffi.
"""

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

COOKIES_PATH = Path("storage/autoru_cookies.json")


def warmup():
    print("Launching browser for Auto.ru cookie warmup...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()

        # Navigate through Auto.ru to build up cookies
        print("  Visiting auto.ru main page...")
        page.goto("https://auto.ru/", wait_until="networkidle", timeout=30000)
        time.sleep(5)

        print("  Visiting used cars listing...")
        page.goto("https://auto.ru/cars/used/", wait_until="networkidle", timeout=30000)
        time.sleep(5)

        print("  Visiting Toyota search...")
        try:
            page.goto("https://auto.ru/cars/toyota/used/?geo_id=213", wait_until="domcontentloaded", timeout=30000)
        except Exception:
            print("  Toyota page timeout, continuing...")
        time.sleep(5)

        # Extract cookies
        cookies = context.cookies()
        browser.close()

    # Save cookies
    cookies_dict = {c["name"]: c["value"] for c in cookies}
    COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    COOKIES_PATH.write_text(json.dumps(cookies_dict, indent=2), encoding="utf-8")

    print(f"Saved {len(cookies_dict)} cookies to {COOKIES_PATH}")
    print(f"Key cookies: {[k for k in cookies_dict if k in ('autoru_sid', '__suid', 'cvcs', 'yandexuid')]}")

    return cookies_dict


if __name__ == "__main__":
    warmup()
