"""Shared HTTP client with browser impersonation for web fetching."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from curl_cffi import CurlOpt
from httpx_curl_cffi import AsyncCurlTransport

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0
IMPERSONATE_BROWSER = "chrome124"


@asynccontextmanager
async def get_impersonating_client(
    timeout: float = DEFAULT_TIMEOUT,
) -> AsyncIterator[httpx.AsyncClient]:
    """
    Create an httpx AsyncClient with browser impersonation.

    Uses curl-cffi under the hood to impersonate Chrome, which helps avoid
    bot detection on many websites.

    Args:
        timeout: Request timeout in seconds. Default 30.

    Yields:
        Configured httpx.AsyncClient with Chrome impersonation.
    """
    transport = AsyncCurlTransport(
        impersonate=IMPERSONATE_BROWSER,
        curl_options={CurlOpt.FRESH_CONNECT: True},
    )

    async with httpx.AsyncClient(
        transport=transport,
        timeout=timeout,
        follow_redirects=True,
    ) as client:
        yield client


async def fetch_url(url: str, timeout: float = DEFAULT_TIMEOUT) -> str:
    """
    Fetch a single URL using browser impersonation.

    Args:
        url: The URL to fetch.
        timeout: Request timeout in seconds.

    Returns:
        The response text (HTML content).

    Raises:
        httpx.HTTPStatusError: If response status is 4xx or 5xx.
        httpx.RequestError: For network errors.
    """
    async with get_impersonating_client(timeout=timeout) as client:
        logger.debug(f"Fetching URL: {url}")
        response = await client.get(url)
        response.raise_for_status()
        return response.text
