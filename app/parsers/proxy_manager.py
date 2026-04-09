"""Proxy management: IP rotation, health checks, dual proxy support."""

from __future__ import annotations

import logging
import os
import time

import httpx

logger = logging.getLogger(__name__)

# Primary proxy
CHANGE_IP_URL = os.environ.get(
    "PROXY_CHANGE_URL",
    "https://changeip.mobileproxy.space/?proxy_key=abf2e5dc61e827256c21191a59a6fe3a",
)

# Secondary proxy (optional)
CHANGE_IP_URL_2 = os.environ.get("PROXY_CHANGE_URL_2", "")

# Track which proxy is active (0 = primary, 1 = secondary)
_active_proxy = 0


def get_proxy_url(index: int | None = None) -> str | None:
    """Get SOCKS5 proxy URL from environment.

    index=None uses active proxy, 0=primary, 1=secondary.
    """
    if index is None:
        index = _active_proxy
    key = "PROXY_STRING" if index == 0 else "PROXY_STRING_2"
    proxy_string = os.environ.get(key, "")
    if proxy_string:
        proxy_type = os.environ.get("PROXY_TYPE", "socks5")
        return f"{proxy_type}://{proxy_string}"
    return None


def switch_proxy() -> int:
    """Switch to the other proxy. Returns new active proxy index."""
    global _active_proxy
    if os.environ.get("PROXY_STRING_2"):
        _active_proxy = 1 - _active_proxy
        logger.info("Switched to proxy %d", _active_proxy + 1)
    return _active_proxy


def change_ip(proxy_index: int | None = None) -> str | None:
    """Request a new IP from the mobile proxy provider.

    Returns the new IP address or None on failure.
    """
    if proxy_index is None:
        proxy_index = _active_proxy
    url = CHANGE_IP_URL if proxy_index == 0 else CHANGE_IP_URL_2
    if not url:
        return None
    try:
        resp = httpx.get(f"{url}&format=json", timeout=20)
        data = resp.json()
        new_ip = data.get("new_ip")
        if new_ip:
            logger.info("Proxy %d IP changed to %s", proxy_index + 1, new_ip)
            time.sleep(3)
            return new_ip
    except Exception:
        logger.warning("Failed to change proxy %d IP", proxy_index + 1, exc_info=True)
    return None


def check_proxy() -> bool:
    """Quick health check of the proxy."""
    proxy_url = get_proxy_url()
    if not proxy_url:
        return True  # No proxy configured

    try:
        resp = httpx.get("http://ip-api.com/json", proxy=proxy_url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            logger.debug("Proxy OK: %s (%s)", data.get("query"), data.get("isp"))
            return True
    except Exception:
        pass

    logger.warning("Proxy health check failed, changing IP...")
    change_ip()
    return False
