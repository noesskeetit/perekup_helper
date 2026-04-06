"""Proxy management: IP rotation, health checks, rate limit handling."""

from __future__ import annotations

import logging
import os
import time

import httpx

logger = logging.getLogger(__name__)

CHANGE_IP_URL = os.environ.get(
    "PROXY_CHANGE_URL",
    "https://changeip.mobileproxy.space/?proxy_key=abf2e5dc61e827256c21191a59a6fe3a",
)


def get_proxy_url() -> str | None:
    """Get SOCKS5 proxy URL from environment."""
    proxy_string = os.environ.get("PROXY_STRING", "")
    if proxy_string:
        proxy_type = os.environ.get("PROXY_TYPE", "socks5")
        return f"{proxy_type}://{proxy_string}"
    return None


def change_ip() -> str | None:
    """Request a new IP from the mobile proxy provider.

    Returns the new IP address or None on failure.
    """
    try:
        resp = httpx.get(f"{CHANGE_IP_URL}&format=json", timeout=20)
        data = resp.json()
        new_ip = data.get("new_ip")
        if new_ip:
            logger.info("Proxy IP changed to %s", new_ip)
            time.sleep(3)  # Wait for IP to propagate
            return new_ip
    except Exception:
        logger.warning("Failed to change proxy IP", exc_info=True)
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
