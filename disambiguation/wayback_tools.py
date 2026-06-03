"""Wayback Machine tools for retrieving archived web content."""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

import httpx

from content_store import (
    DEFAULT_CONTEXT_WORDS,
    ContentHandle,
    StoredContent,
    create_content_handle,
    extract_text_from_html,
    get_store,
    register_refetch_wayback,
)
from http_client import get_impersonating_client

logger = logging.getLogger(__name__)

CDX_API_URL = "https://web.archive.org/cdx/search/cdx"
WAYBACK_BASE_URL = "https://web.archive.org/web"
DEFAULT_TIMEOUT = 60.0


@dataclass
class WaybackCapture:
    """Represents a single capture from the Wayback Machine."""

    timestamp: str
    original_url: str
    urlkey: str
    mimetype: str
    statuscode: int
    digest: str
    length: int
    readable_date: str
    wayback_url: str
    dupe_count: int | None = None


@dataclass
class WaybackSearchResult:
    """Result of a Wayback Machine CDX search."""

    captures: list[WaybackCapture]
    total_returned: int
    has_more: bool
    resume_key: str | None
    query_url: str


def _parse_timestamp(timestamp: str) -> str:
    """Convert a CDX timestamp to a readable date string."""
    try:
        dt = datetime.strptime(timestamp, "%Y%m%d%H%M%S")
        return dt.strftime("%B %d, %Y %H:%M:%S")
    except ValueError:
        # Handle partial timestamps
        formats = [
            ("%Y%m%d%H%M", "%B %d, %Y %H:%M"),
            ("%Y%m%d%H", "%B %d, %Y %H:00"),
            ("%Y%m%d", "%B %d, %Y"),
            ("%Y%m", "%B %Y"),
            ("%Y", "%Y"),
        ]
        for fmt_in, fmt_out in formats:
            try:
                dt = datetime.strptime(timestamp[: len(fmt_in.replace("%", ""))], fmt_in)
                return dt.strftime(fmt_out)
            except ValueError:
                continue
        return timestamp


async def wayback_search(
    url: Annotated[
        str,
        "URL or domain to search. Examples: 'example.org', 'example.org/path/', 'example.org/page.html'",
    ],
    match_type: Annotated[
        Literal["exact", "prefix", "host", "domain"],
        """How to match the URL:
        - exact: only this exact URL (default)
        - prefix: all URLs beginning with this path (example.org/staff/ matches example.org/staff/john.html)
        - host: all URLs from this specific host only (www.example.org, but NOT blog.example.org)
        - domain: this domain AND all subdomains (example.org matches www.example.org, blog.example.org, etc.)

        For searching an entire website, use 'domain'. For a specific section, use 'prefix'.""",
    ] = "exact",
    from_date: Annotated[
        str | None,
        """Start of date range (inclusive). Format: 1-14 digits as yyyyMMddhhmmss.
        Examples: '2015' (year), '201506' (month), '20150615' (day), '2015061514' (hour)""",
    ] = None,
    to_date: Annotated[
        str | None,
        """End of date range (inclusive). Same format as from_date.""",
    ] = None,
    filter_statuscode: Annotated[
        str | None,
        """Filter by HTTP status code. Prefix with ! to exclude.
        Examples: '200' (only 200s), '!404' (exclude 404s), '200|301' (200 or 301)""",
    ] = None,
    filter_mimetype: Annotated[
        str | None,
        """Filter by MIME type (regex). Prefix with ! to exclude.
        Examples: 'text/html', '!warc/revisit', 'text/.*' (any text type)""",
    ] = None,
    collapse: Annotated[
        str | None,
        """Deduplicate adjacent results by field. Format: 'field' or 'field:N' (first N chars).

        For timestamps (yyyyMMddhhmmss format):
        - timestamp:4 = one capture per year
        - timestamp:6 = one capture per month
        - timestamp:8 = one capture per day
        - timestamp:10 = one capture per hour

        Other useful options:
        - digest = only unique content (by hash)
        - urlkey = one per unique URL""",
    ] = None,
    limit: Annotated[
        int,
        """Max results to return. Positive = first N, negative = last N (e.g., -10 for 10 most recent).""",
    ] = 50,
    resume_key: Annotated[
        str | None,
        """Continuation token from previous search. Pass this to get next page of results.""",
    ] = None,
    show_dupe_count: Annotated[
        bool,
        """If True, adds count of duplicate captures (by digest) for each result.""",
    ] = False,
) -> WaybackSearchResult:
    """
    Search Wayback Machine CDX index for archived webpage captures.

    Returns a list of captures (archived snapshots) matching the query.
    Use the timestamp from results with wayback_fetch() to retrieve content.

    TYPICAL WORKFLOW:
    1. Search with match_type="domain" to explore what's archived for a site
    2. Use collapse="timestamp:6" to avoid thousands of duplicate monthly crawls
    3. Filter with filter_statuscode="200" and filter_mimetype="text/html" for usable pages
    4. Use from_date/to_date to focus on relevant time periods
    5. Fetch specific pages with wayback_fetch() using the timestamp
    """
    # Input validation — LLM sometimes passes URLs with trailing newlines
    url = url.strip()
    if not url:
        logger.warning("Wayback search: empty URL, returning no results")
        return WaybackSearchResult(
            captures=[], total_returned=0, has_more=False, resume_key=None, query_url=""
        )

    params: dict[str, str | int] = {
        "url": url,
        "output": "json",
        "showResumeKey": "true",
        "matchType": match_type,
        "limit": limit,
    }

    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date
    if filter_statuscode:
        params["filter"] = f"statuscode:{filter_statuscode}"
    if filter_mimetype:
        # Add as additional filter if statuscode filter exists
        if "filter" in params:
            params["filter"] = f"{params['filter']}&filter=mimetype:{filter_mimetype}"
        else:
            params["filter"] = f"mimetype:{filter_mimetype}"
    if collapse:
        params["collapse"] = collapse
    if resume_key:
        params["resumeKey"] = resume_key
    if show_dupe_count:
        params["showDupeCount"] = "true"

    logger.info(f"Wayback search: {url!r} (match={match_type}, from={from_date}, to={to_date})")
    logger.debug(f"Wayback CDX search params: {params}")

    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        response = await client.get(CDX_API_URL, params=params)

        # Handle HTTP errors gracefully - return empty results instead of crashing
        # This allows the agent to continue with other search strategies
        if response.status_code >= 400:
            status_code = response.status_code
            if status_code == 403:
                logger.warning(
                    f"Wayback CDX returned 403 FORBIDDEN for {url} - may be rate limited or blocked"
                )
            elif status_code == 404:
                logger.info(f"Wayback CDX returned 404 for {url} - no captures found")
            elif status_code == 429:
                logger.warning(
                    f"Wayback CDX returned 429 TOO MANY REQUESTS for {url} - rate limited"
                )
            elif status_code >= 500:
                logger.warning(f"Wayback CDX returned {status_code} server error for {url}")
            else:
                logger.warning(f"Wayback CDX returned {status_code} for {url}")

            return WaybackSearchResult(
                captures=[],
                total_returned=0,
                has_more=False,
                resume_key=None,
                query_url=str(response.url),
            )

        data = response.json()

    if not data:
        return WaybackSearchResult(
            captures=[],
            total_returned=0,
            has_more=False,
            resume_key=None,
            query_url=str(response.url),
        )

    # First row is header, last row may be resume key
    header = data[0]
    rows = data[1:]

    # Check for resume key in last row
    result_resume_key = None
    has_more = False
    if rows and len(rows[-1]) == 2 and rows[-1][0] == "":
        # Resume key is in format ["", "resumeKey"]
        result_resume_key = rows[-1][1]
        rows = rows[:-1]
        has_more = True

    # Map header positions
    field_map = {name: idx for idx, name in enumerate(header)}

    def _safe_get(row: list, field: str, default_idx: int, default: str = "") -> str:
        """Safely get a field from a CDX row, returning default if out of bounds."""
        idx = field_map.get(field, default_idx)
        if idx < len(row):
            return row[idx]
        return default

    captures = []
    for row in rows:
        if len(row) < 2:
            logger.debug(f"Skipping malformed CDX row: {row}")
            continue

        timestamp = _safe_get(row, "timestamp", 0)
        original = _safe_get(row, "original", 1)
        if not timestamp or not original:
            logger.debug(f"Skipping CDX row with missing timestamp/url: {row}")
            continue

        statuscode_str = _safe_get(row, "statuscode", 4, "0")
        length_str = _safe_get(row, "length", 6, "0")

        try:
            statuscode = int(statuscode_str) if statuscode_str else 0
        except ValueError:
            statuscode = 0
        try:
            length = int(length_str) if length_str else 0
        except ValueError:
            length = 0

        capture = WaybackCapture(
            timestamp=timestamp,
            original_url=original,
            urlkey=_safe_get(row, "urlkey", 0),
            mimetype=_safe_get(row, "mimetype", 3),
            statuscode=statuscode,
            digest=_safe_get(row, "digest", 5),
            length=length,
            readable_date=_parse_timestamp(timestamp),
            wayback_url=f"{WAYBACK_BASE_URL}/{timestamp}/{original}",
            dupe_count=int(_safe_get(row, "dupecount", -1, "0"))
            if "dupecount" in field_map and show_dupe_count
            else None,
        )
        captures.append(capture)

    logger.info(f"Wayback search returned {len(captures)} captures for {url}")

    return WaybackSearchResult(
        captures=captures,
        total_returned=len(captures),
        has_more=has_more,
        resume_key=result_resume_key,
        query_url=str(response.url),
    )


async def _fetch_wayback_content(url: str, timestamp: str) -> tuple[str, str] | None:
    """Fetch content from Wayback Machine. Returns (html, text) or None on error."""
    # Use id_ to get raw content without Wayback toolbar
    wayback_url = f"{WAYBACK_BASE_URL}/{timestamp}id_/{url}"

    logger.debug(f"Fetching Wayback content: {wayback_url}")

    async with get_impersonating_client(timeout=DEFAULT_TIMEOUT) as client:
        response = await client.get(wayback_url)

        # Handle HTTP errors gracefully - return None instead of crashing
        if response.status_code >= 400:
            status_code = response.status_code
            if status_code == 403:
                logger.warning(f"Wayback fetch returned 403 FORBIDDEN for {wayback_url}")
            elif status_code == 404:
                logger.warning(
                    f"Wayback fetch returned 404 for {wayback_url} - capture may not exist"
                )
            elif status_code == 429:
                logger.warning("Wayback fetch returned 429 TOO MANY REQUESTS - rate limited")
            elif status_code >= 500:
                logger.warning(f"Wayback fetch returned {status_code} server error")
            else:
                logger.warning(f"Wayback fetch returned {status_code} for {wayback_url}")
            return None

        html_content = response.text

    # Extract text using trafilatura
    text_content = extract_text_from_html(html_content)

    return html_content, text_content


async def _refetch_wayback_url(url: str, timestamp: str) -> None:
    """Refetch a wayback URL and store it. Called for auto-refetch."""
    result = await _fetch_wayback_content(url, timestamp)
    if result is None:
        logger.warning(f"Failed to refetch wayback content for {url} @ {timestamp}")
        return

    html_content, text_content = result
    store = get_store()
    content_id = store._generate_id(url, "wayback", timestamp)

    content = StoredContent(
        content_id=content_id,
        url=url,
        source="wayback",
        fetched_at=datetime.now(),
        raw_html=html_content,
        extracted_text=text_content,
        title=None,
        fetch_params={"timestamp": timestamp},
    )
    store.store(content)


# Register the refetch callback
register_refetch_wayback(_refetch_wayback_url)


async def wayback_fetch(
    url: Annotated[str, "The original URL to fetch (not the wayback URL)"],
    timestamp: Annotated[
        str,
        "The 14-digit timestamp from wayback_search results (e.g., '20150615143000')",
    ],
    find: Annotated[
        str | None,
        """Optional: regex pattern to search immediately after fetching.
Examples: 'John Smith', 'superintendent|principal'""",
    ] = None,
    context_words: Annotated[
        int,
        "Words of context around matches when using find. Default 30.",
    ] = DEFAULT_CONTEXT_WORDS,
) -> ContentHandle:
    """
    Fetch an archived page from Wayback Machine and store for exploration.

    Returns a content handle. Use content_search() or content_read() to
    explore, or use find= to search immediately.

    If the fetch fails (HTTP error), returns a handle with empty content
    and an error message in the preview.
    """
    result = await _fetch_wayback_content(url, timestamp)

    store = get_store()
    content_id = store._generate_id(url, "wayback", timestamp)

    if result is None:
        # Return a handle indicating the fetch failed
        logger.warning(f"Wayback fetch failed for {url} @ {timestamp}")
        return ContentHandle(
            content_id=content_id,
            url=url,
            source="wayback",
            title=None,
            content_length=0,
            preview=f"[FETCH FAILED] Could not retrieve content from Wayback Machine for {url} at {timestamp}. Try a different timestamp or URL.",
            find_matches=None,
            find_total=None,
        )

    html_content, text_content = result
    content = StoredContent(
        content_id=content_id,
        url=url,
        source="wayback",
        fetched_at=datetime.now(),
        raw_html=html_content,
        extracted_text=text_content,
        title=None,
        fetch_params={"timestamp": timestamp},
    )
    store.store(content)

    handle = create_content_handle(content, find_pattern=find, context_words=context_words)

    logger.info(
        f"Stored wayback content {content_id} from {url} @ {timestamp} ({len(text_content)} chars)"
    )

    return handle
