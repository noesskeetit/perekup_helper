"""Debug script: fetch one Auto.ru page and dump field names around offer IDs.

Run from project root:
    python -m scripts.debug_autoru_html

Requires: curl_cffi, proxy configured in .env
"""

import json
import os
import re
import sys
from collections import Counter
from pathlib import Path


def main():
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        print("ERROR: curl_cffi required. pip install curl_cffi")
        sys.exit(1)

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    proxy_string = os.environ.get("PROXY_STRING", "")
    proxy_type = os.environ.get("PROXY_TYPE", "socks5")

    session = cffi_requests.Session(impersonate="chrome")
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8",
            "Referer": "https://auto.ru/",
        }
    )

    if proxy_string:
        proxy_url = f"{proxy_type}://{proxy_string}"
        session.proxies = {"http": proxy_url, "https": proxy_url}
        print(f"Using proxy: {proxy_type}://***")

    # Load cookies
    cookies_path = Path("storage/autoru_cookies.json")
    if cookies_path.exists():
        cookies = json.loads(cookies_path.read_text(encoding="utf-8"))
        session.cookies.update(cookies)
        print(f"Loaded {len(cookies)} cookies")

    # Warmup
    print("Warming up...")
    session.get("https://auto.ru/", timeout=20)

    # Fetch a listing page
    url = "https://auto.ru/cars/toyota/used/?geo_id=213&price_from=500000&price_to=800000"
    print(f"Fetching: {url}")
    resp = session.get(url, timeout=60)
    html = resp.text
    print(f"Got {len(html)} bytes, status={resp.status_code}")

    if "captcha" in str(resp.url).lower() or resp.status_code == 403:
        print("CAPTCHA! Cannot proceed.")
        sys.exit(1)

    # Save full HTML for analysis
    Path("storage/debug_autoru.html").write_text(html, encoding="utf-8")
    print("Saved to storage/debug_autoru.html")

    # Find offer IDs
    url_pattern = re.compile(r"auto\.ru/cars/used/sale/(\w+)/(\w+)/(\d+)-([a-f0-9]+)/")
    offer_ids = {}
    for m in url_pattern.finditer(html):
        nid = m.group(3)
        if nid not in offer_ids:
            offer_ids[nid] = m.group(0)

    print(f"\nFound {len(offer_ids)} unique offer IDs")

    if not offer_ids:
        # Look for other patterns
        print("\nSearching for alternative patterns...")
        for pattern_name, pattern in [
            ("sale_id", r'"sale_id":"(\d+)"'),
            ("offer_id", r'"offer_id":"(\d+)"'),
            ("id_numeric", r'"id":"(\d{8,})"'),
        ]:
            matches = re.findall(pattern, html)
            print(f"  {pattern_name}: {len(matches)} matches")
        sys.exit(1)

    # For each offer ID, dump surrounding context with different radii
    first_id = list(offer_ids.keys())[0]
    print(f"\n=== Analyzing offer ID: {first_id} ===")

    # Find all occurrences of this ID
    id_pattern = re.compile(rf'"{first_id}"')
    occurrences = list(id_pattern.finditer(html))
    print(f"Found {len(occurrences)} occurrences of this ID in HTML")

    # For each occurrence, extract JSON keys nearby
    for i, m in enumerate(occurrences[:5]):
        print(f"\n--- Occurrence #{i + 1} at position {m.start()} ---")

        # Try different radii
        for radius in [3000, 5000, 10000, 15000, 20000]:
            start = max(0, m.start() - radius)
            end = min(len(html), m.end() + radius)
            chunk = html[start:end]

            # Count JSON keys in chunk
            keys = re.findall(r'"([a-z_]+)":', chunk)
            key_counts = Counter(keys)

            # Check for specific fields
            target_fields = [
                "displacement",
                "engine_volume",
                "power",
                "engine_power",
                "power_hp",
                "gear_type",
                "drive",
                "drive_type",
                "body_type",
                "color_hex",
                "color",
                "steering_wheel",
                "owners_number",
                "owners_count",
                "pts",
                "pts_type",
                "description",
                "seller_comment",
                "seller_type",
                "creation_date",
                "created",
                "tech_param",
                "configuration",
                "vehicle_info",
                "car_info",
                "documents",
                "additional_info",
                "mark_info",
                "model_info",
                "super_gen",
                "engine_type",
                "transmission",
                "price",
                "year",
                "mileage",
                "vin",
            ]

            found = {f: key_counts.get(f, 0) for f in target_fields if key_counts.get(f, 0) > 0}
            if found:
                print(f"  Radius +-{radius}: found keys: {found}")

        # Dump a focused extract around this occurrence
        start = max(0, m.start() - 2000)
        end = min(len(html), m.end() + 2000)
        chunk = html[start:end]

        # Find all "key": patterns
        all_keys = re.findall(r'"([a-z_][a-z_0-9]*)":', chunk)
        unique_keys = sorted(set(all_keys))
        print(f"\n  All JSON keys in +-2000 chars ({len(unique_keys)} unique):")
        print(f"  {unique_keys}")

    # Global search for tech fields
    print("\n\n=== Global field search in full HTML ===")
    global_targets = [
        ("displacement", r'"displacement":(\d+)'),
        ("engine_volume", r'"engine_volume":(\d+)'),
        ("power (standalone)", r'"power":(\d+)'),
        ("engine_power", r'"engine_power":(\d+)'),
        ("gear_type", r'"gear_type":"([^"]+)"'),
        ("drive", r'"drive":"([^"]+)"'),
        ("body_type", r'"body_type":"([^"]+)"'),
        ("body_type_group", r'"body_type_group":"([^"]+)"'),
        ("color_hex", r'"color_hex":"([^"]+)"'),
        ("color obj", r'"color":\{'),
        ("color name", r'"color_name":"([^"]+)"'),
        ("steering_wheel", r'"steering_wheel":"([^"]+)"'),
        ("wheel", r'"wheel":"([^"]+)"'),
        ("owners_number", r'"owners_number":(\d+)'),
        ("owners_count", r'"owners_count":(\d+)'),
        ("pts", r'"pts":"([^"]+)"'),
        ("pts_type", r'"pts_type":"([^"]+)"'),
        ("custom_cleared", r'"custom_cleared":(true|false)'),
        ("description", r'"description":"'),
        ("seller_comment", r'"seller_comment":"'),
        ("seller_type", r'"seller_type":"([^"]+)"'),
        ("creation_date", r'"creation_date":"([^"]+)"'),
        ("created", r'"created":"([^"]+)"'),
        ("tech_param", r'"tech_param":\{'),
        ("configuration", r'"configuration":\{'),
        ("vehicle_info", r'"vehicle_info":\{'),
        ("car_info", r'"car_info":\{'),
        ("documents", r'"documents":\{'),
        ("additional_info", r'"additional_info":\{'),
        ("horse_power", r'"horse_power":(\d+)'),
        ("horsepower", r'"horsepower":(\d+)'),
        ("hp", r'"hp":(\d+)'),
        ("engine_hp", r'"engine_hp":(\d+)'),
        ("volume", r'"volume":(\d+)'),
    ]

    for name, pattern in global_targets:
        matches = re.findall(pattern, html)
        if matches:
            sample = matches[:3]
            print(f"  {name}: {len(matches)} matches, samples: {sample}")

    # Also extract a full JSON snippet if possible
    print("\n\n=== Trying to extract __SSR_DATA__ ===")
    ssr_m = re.search(
        r"window\.(__SSR_DATA__|__INITIAL_STATE__)\s*=\s*(\{.+?\});\s*(?:</script>|$)",
        html,
        re.DOTALL,
    )
    if ssr_m:
        print(f"Found {ssr_m.group(1)}, {len(ssr_m.group(2))} chars")
        try:
            data = json.loads(ssr_m.group(2))
            print(f"Parsed JSON, top keys: {list(data.keys())[:20]}")
            # Dump structure recursively (first few levels)

            def dump_keys(obj, prefix="", depth=0):
                if depth > 4:
                    return
                if isinstance(obj, dict):
                    for k, v in list(obj.items())[:30]:
                        typ = type(v).__name__
                        print(f"  {prefix}{k}: {typ}")
                        if isinstance(v, (dict, list)):
                            dump_keys(v, prefix + "  ", depth + 1)
                elif isinstance(obj, list) and obj:
                    print(f"  {prefix}[0]:")
                    dump_keys(obj[0], prefix + "  ", depth + 1)

            dump_keys(data)
        except json.JSONDecodeError as e:
            print(f"Failed to parse JSON: {e}")
    else:
        print("No __SSR_DATA__ or __INITIAL_STATE__ found")

    # Try alternative: look for JSON arrays of listings
    print("\n=== Looking for listing arrays ===")
    for pattern_name, pattern in [
        ("listing array", r'"listing":\['),
        ("offers array", r'"offers":\['),
        ("items array", r'"items":\['),
        ("results array", r'"results":\['),
        ("search_result", r'"searchResult"'),
        ("grouping", r'"grouping"'),
    ]:
        matches = list(re.finditer(pattern, html))
        if matches:
            print(f"  {pattern_name}: {len(matches)} matches at positions {[m.start() for m in matches[:3]]}")


if __name__ == "__main__":
    main()
