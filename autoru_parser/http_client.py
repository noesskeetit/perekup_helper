import asyncio
import logging
import random

import httpx

from avito_parser.user_agents import get_random_user_agent

from .config import settings

logger = logging.getLogger(__name__)


class AutoruHttpClient:
    """HTTP client with rate-limiting, rotating user-agents, and retry logic for auto.ru."""

    def __init__(self):
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0),
                follow_redirects=True,
                limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _build_headers(self) -> dict[str, str]:
        return {
            "User-Agent": get_random_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Referer": settings.autoru_base_url,
        }

    async def _delay(self):
        delay = random.uniform(settings.request_delay_min, settings.request_delay_max)
        logger.debug("Sleeping %.1f seconds between requests", delay)
        await asyncio.sleep(delay)

    async def get(self, url: str, max_retries: int = 3) -> str | None:
        """Fetch a page with rate-limiting and retries. Returns HTML or None."""
        client = await self._get_client()

        for attempt in range(1, max_retries + 1):
            await self._delay()
            headers = self._build_headers()

            try:
                response = await client.get(url, headers=headers)

                if response.status_code == 200:
                    return response.text

                if response.status_code == 429:
                    wait = min(30, 5 * attempt)
                    logger.warning("Rate limited (429), waiting %d seconds (attempt %d/%d)", wait, attempt, max_retries)
                    await asyncio.sleep(wait)
                    continue

                if response.status_code == 403:
                    logger.warning("Forbidden (403) for %s, attempt %d/%d", url, attempt, max_retries)
                    await asyncio.sleep(10 * attempt)
                    continue

                logger.warning("HTTP %d for %s", response.status_code, url)
                return None

            except httpx.HTTPError as e:
                logger.error("HTTP error for %s: %s (attempt %d/%d)", url, e, attempt, max_retries)
                if attempt == max_retries:
                    return None
                await asyncio.sleep(5 * attempt)

        logger.error("Max retries exhausted for %s", url)
        return None
