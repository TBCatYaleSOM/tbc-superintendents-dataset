"""Search and fetch tools for superintendent disambiguation."""

import asyncio
import logging
import os
from datetime import datetime
from typing import Annotated, Any

from tavily import TavilyClient

from content_store import (
    DEFAULT_CONTEXT_WORDS,
    ContentHandle,
    StoredContent,
    create_content_handle,
    extract_text_from_html,
    get_store,
    register_refetch_direct,
)
from http_client import fetch_url

logger = logging.getLogger(__name__)


def _get_tavily_client() -> TavilyClient:
    """Get configured Tavily client."""
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        raise ValueError("TAVILY_API_KEY environment variable is required")
    return TavilyClient(api_key=api_key)


def tavily_search(
    query: str,
    search_depth: str = "advanced",
    max_results: int = 10,
    include_domains: list[str] | None = None,
) -> dict[str, Any]:
    """
    Search the web for information using Tavily.

    Use this tool to find information about superintendents, their career history,
    district transitions, and professional background. Craft specific queries
    using the superintendent's name, districts, and years.

    Args:
        query: The search query. Use quotes around names and district names
               for exact matching. Example: '"John Smith" superintendent "Oak Valley USD"'
        search_depth: Search depth - "basic" for quick search, "advanced" for
                      comprehensive results with more context per source.
        max_results: Maximum number of results to return (1-20). Use higher
                     values when searching for common names.
        include_domains: Optional list of domains to restrict search to.
                        Example: ["stocktonusd.net", "fresnounified.org"]
                        Use this to search specific district websites discovered in Phase 1.

    Returns:
        Dictionary containing:
        - results: List of search results, each with title, url, content, score
        - answer: AI-generated summary if available
    """
    # Input validation — LLM sometimes generates empty or malformed queries
    query = query.strip()
    if not query:
        logger.warning("Tavily search: empty query, returning no results")
        return {"results": [], "answer": None}

    search_depth = search_depth.strip().lower()
    if search_depth not in ("basic", "advanced"):
        logger.warning(f"Tavily search: invalid depth {search_depth!r}, defaulting to 'advanced'")
        search_depth = "advanced"

    max_results = max(1, min(20, max_results))

    domains_str = f", domains={include_domains}" if include_domains else ""
    logger.info(f"Tavily search: {query!r} (depth={search_depth}, max={max_results}{domains_str})")
    client = _get_tavily_client()
    response = client.search(
        query=query,
        search_depth=search_depth,
        max_results=max_results,
        include_answer=True,
        include_domains=include_domains or [],
    )
    results = response.get("results", [])
    logger.info(f"Tavily search returned {len(results)} results")
    return {
        "results": results,
        "answer": response.get("answer"),
    }


async def _refetch_direct_url(url: str) -> None:
    """Refetch a single URL via direct HTTP and store it."""
    try:
        html_content = await fetch_url(url)
    except Exception as e:
        logger.warning(f"Failed to refetch {url}: {e}")
        return

    text = extract_text_from_html(html_content) if html_content else ""
    if not text:
        text = html_content

    store = get_store()
    content_id = store._generate_id(url, "direct")
    content = StoredContent(
        content_id=content_id,
        url=url,
        source="direct",
        fetched_at=datetime.now(),
        raw_html=html_content,
        extracted_text=text,
        title=None,
        fetch_params=None,
    )
    store.store(content)


# Register the refetch callback
register_refetch_direct(_refetch_direct_url)


async def fetch_urls(
    urls: Annotated[
        list[str],
        "List of URLs to fetch content from (max 20).",
    ],
    find: Annotated[
        str | None,
        """Optional: regex pattern to search for immediately after fetching.
If provided, results include matching excerpts with context.
Examples: 'John Smith', 'superintendent|principal'
This saves a separate content_search() call.""",
    ] = None,
    context_words: Annotated[
        int,
        "Words of context around matches when using find. Default 30.",
    ] = DEFAULT_CONTEXT_WORDS,
) -> list[ContentHandle]:
    """
    Fetch and store content from URLs for exploration.

    Uses direct HTTP fetching with browser impersonation to retrieve content.
    Returns content handles (not full content). Use content_search() or
    content_read() to explore the stored content, or use the find= parameter
    to search immediately.

    EXAMPLE:
        # Fetch and immediately search for a name
        handles = fetch_urls(
            ["https://district.org/staff"],
            find="John Smith"
        )
        # handles[0].find_matches contains matches with context

        # Or fetch first, search later
        handles = fetch_urls(["https://district.org/staff"])
        results = content_search(handles[0].content_id, "John Smith")
    """
    if not urls:
        return []

    # Limit to 20 URLs
    urls = urls[:20]

    store = get_store()
    handles: list[ContentHandle] = []
    failed_urls: list[str] = []

    # Fetch URLs concurrently
    async def fetch_single(url: str) -> tuple[str, str | None, Exception | None]:
        """Fetch a single URL, returning (url, html, error)."""
        try:
            html = await fetch_url(url)
            return (url, html, None)
        except Exception as e:
            return (url, None, e)

    results = await asyncio.gather(*[fetch_single(url) for url in urls])

    for url, html_content, error in results:
        if error is not None:
            logger.warning(f"Failed to fetch {url}: {error}")
            failed_urls.append(url)
            continue

        # Extract text from HTML
        text = extract_text_from_html(html_content) if html_content else ""
        if not text:
            text = html_content or ""

        content_id = store._generate_id(url, "direct")
        content = StoredContent(
            content_id=content_id,
            url=url,
            source="direct",
            fetched_at=datetime.now(),
            raw_html=html_content,
            extracted_text=text,
            title=None,
            fetch_params=None,
        )
        store.store(content)

        handle = create_content_handle(content, find_pattern=find, context_words=context_words)
        handles.append(handle)

        logger.info(f"Stored content {content_id} from {url} ({len(text)} chars)")

    if failed_urls:
        logger.warning(f"Failed to fetch {len(failed_urls)} URLs: {failed_urls}")

    return handles
