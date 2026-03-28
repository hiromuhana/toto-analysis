"""Base collector with rate limiting, retries, and caching."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx

from toto.config import HTTP_TIMEOUT, MAX_RETRIES, RATE_LIMIT_SECONDS
from toto.utils import cache

logger = logging.getLogger(__name__)


class BaseCollector(ABC):
    """Abstract base for all data collectors.

    Provides async HTTP with rate limiting, retry logic, and caching.
    Subclasses implement collect() to perform domain-specific scraping/API calls.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._last_request_time: float = 0.0

    async def _rate_limit(self) -> None:
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_request_time
        if elapsed < RATE_LIMIT_SECONDS:
            await asyncio.sleep(RATE_LIMIT_SECONDS - elapsed)
        self._last_request_time = asyncio.get_event_loop().time()

    async def fetch(
        self,
        url: str,
        *,
        cache_key: str | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> str:
        """Fetch URL content with rate limiting, caching, and retries.

        Args:
            url: Target URL.
            cache_key: Optional cache key. If provided, checks cache first.
            headers: Optional HTTP headers.
            params: Optional query parameters.

        Returns:
            Response text content.

        Raises:
            httpx.HTTPStatusError: After all retries exhausted.
        """
        if cache_key:
            cached = cache.get(cache_key)
            if cached is not None:
                logger.info("[%s] Cache hit: %s", self.name, cache_key)
                return cached

        last_error: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                await self._rate_limit()
                async with httpx.AsyncClient(
                    timeout=HTTP_TIMEOUT,
                    follow_redirects=True,
                ) as client:
                    resp = await client.get(
                        url,
                        headers=headers or self._default_headers(),
                        params=params,
                    )
                    resp.raise_for_status()
                    text = resp.text

                if cache_key:
                    cache.set(cache_key, text)

                return text
            except (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException) as e:
                last_error = e
                logger.warning(
                    "[%s] Attempt %d/%d failed for %s: %s",
                    self.name, attempt, MAX_RETRIES, url, e,
                )
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RATE_LIMIT_SECONDS * attempt)

        raise last_error  # type: ignore[misc]

    def _default_headers(self) -> dict[str, str]:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        }

    @abstractmethod
    async def collect(self, toto_round: int, **kwargs: Any) -> Any:
        """Collect data for a given toto round.

        Args:
            toto_round: The toto round number to collect data for.

        Returns:
            Collected data in the appropriate schema format.
        """
        ...
