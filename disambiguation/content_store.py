"""Content store for managing fetched web content with LRU caching."""

import hashlib
import logging
import re
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Callable, Literal

import trafilatura

logger = logging.getLogger(__name__)

PREVIEW_LENGTH = 300
DEFAULT_CONTEXT_WORDS = 30
DEFAULT_MAX_MATCHES = 10
DEFAULT_READ_LENGTH = 3000
MAX_READ_LENGTH = 10000
LRU_CACHE_SIZE = 250


@dataclass
class StoredContent:
    """Content stored in the cache."""

    content_id: str
    url: str
    source: Literal["direct", "wayback"]
    fetched_at: datetime
    raw_html: str | None
    extracted_text: str
    title: str | None
    fetch_params: dict | None = None


@dataclass
class SearchMatch:
    """A single match with word context."""

    matched_text: str
    context_before: str
    context_after: str
    char_offset: int
    snippet: str


@dataclass
class ContentHandle:
    """Lightweight reference to stored content, returned by fetch tools."""

    content_id: str
    url: str
    source: str
    title: str | None
    content_length: int
    preview: str
    find_matches: list[SearchMatch] | None = None
    find_total: int | None = None


@dataclass
class ContentSearchResult:
    """Result of searching within stored content."""

    content_id: str
    total_matches: int
    matches: list[SearchMatch]
    truncated: bool


@dataclass
class ContentReadResult:
    """Result of reading a section of stored content."""

    content_id: str
    text: str
    start_offset: int
    end_offset: int
    total_length: int
    has_more_before: bool
    has_more_after: bool


@dataclass
class ContentInfo:
    """Summary info for listing stored content."""

    content_id: str
    url: str
    source: str
    title: str | None
    content_length: int
    fetched_at: datetime


class ContentStore:
    """LRU cache for fetched web content, max 50 items."""

    def __init__(self, max_size: int = LRU_CACHE_SIZE):
        self._store: OrderedDict[str, StoredContent] = OrderedDict()
        self._max_size = max_size
        self._evicted: dict[str, dict] = {}

    def _generate_id(self, url: str, source: str, timestamp: str | None = None) -> str:
        """Generate a unique content ID."""
        key = f"{source}:{url}"
        if timestamp:
            key = f"{key}:{timestamp}"
        return hashlib.sha256(key.encode()).hexdigest()[:12]

    def store(self, content: StoredContent) -> str:
        """Store content and return its ID. Evicts old items if at capacity."""
        content_id = content.content_id

        # If already exists, move to end (most recent)
        if content_id in self._store:
            self._store.move_to_end(content_id)
            self._store[content_id] = content
            return content_id

        # Evict oldest if at capacity
        while len(self._store) >= self._max_size:
            oldest_id, oldest_content = self._store.popitem(last=False)
            self._evicted[oldest_id] = {
                "url": oldest_content.url,
                "source": oldest_content.source,
                "fetch_params": oldest_content.fetch_params,
            }
            logger.debug(f"Evicted content {oldest_id} from cache")

        self._store[content_id] = content
        # Remove from evicted if it was re-added
        self._evicted.pop(content_id, None)
        return content_id

    def get(self, content_id: str) -> StoredContent | None:
        """Get content by ID, moving it to end of LRU."""
        if content_id in self._store:
            self._store.move_to_end(content_id)
            return self._store[content_id]
        return None

    def get_evicted_info(self, content_id: str) -> dict | None:
        """Get info about evicted content for refetching."""
        return self._evicted.get(content_id)

    def list_all(self) -> list[ContentInfo]:
        """List all stored content."""
        return [
            ContentInfo(
                content_id=c.content_id,
                url=c.url,
                source=c.source,
                title=c.title,
                content_length=len(c.extracted_text),
                fetched_at=c.fetched_at,
            )
            for c in self._store.values()
        ]

    def clear(self) -> None:
        """Clear all stored content."""
        self._store.clear()
        self._evicted.clear()


# Module-level singleton
_store = ContentStore()


def get_store() -> ContentStore:
    """Get the global content store instance."""
    return _store


def _extract_word_context(
    text: str,
    match_start: int,
    match_end: int,
    context_words: int,
) -> tuple[str, str]:
    """Extract N words before and after a match position.

    Returns (context_before, context_after) as strings.
    """
    # Get text before match
    text_before = text[:match_start]
    words_before = text_before.split()
    context_before_words = words_before[-context_words:] if words_before else []
    context_before = " ".join(context_before_words)

    # Get text after match
    text_after = text[match_end:]
    words_after = text_after.split()
    context_after_words = words_after[:context_words] if words_after else []
    context_after = " ".join(context_after_words)

    return context_before, context_after


def _search_content(
    text: str,
    pattern: str,
    context_words: int,
    max_matches: int,
) -> tuple[list[SearchMatch], int]:
    """Search text and return matches with word context.

    Returns (matches, total_count). total_count may be > len(matches) if truncated.
    """
    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        logger.warning(f"Invalid regex pattern '{pattern}': {e}, using literal")
        compiled = re.compile(re.escape(pattern), re.IGNORECASE)

    all_matches = list(compiled.finditer(text))
    total_count = len(all_matches)

    matches: list[SearchMatch] = []
    for m in all_matches[:max_matches]:
        context_before, context_after = _extract_word_context(
            text, m.start(), m.end(), context_words
        )

        # Build snippet with ellipsis
        prefix = "..." if context_before else ""
        suffix = "..." if context_after else ""
        snippet = f"{prefix}{context_before} **{m.group()}** {context_after}{suffix}"

        matches.append(
            SearchMatch(
                matched_text=m.group(),
                context_before=context_before,
                context_after=context_after,
                char_offset=m.start(),
                snippet=snippet.strip(),
            )
        )

    return matches, total_count


def create_content_handle(
    content: StoredContent,
    find_pattern: str | None = None,
    context_words: int = DEFAULT_CONTEXT_WORDS,
    max_matches: int = DEFAULT_MAX_MATCHES,
) -> ContentHandle:
    """Create a ContentHandle from stored content, optionally with search results."""
    preview = content.extracted_text[:PREVIEW_LENGTH]
    if len(content.extracted_text) > PREVIEW_LENGTH:
        preview = preview.rsplit(" ", 1)[0] + "..."

    find_matches = None
    find_total = None

    if find_pattern:
        matches, total = _search_content(
            content.extracted_text, find_pattern, context_words, max_matches
        )
        find_matches = matches
        find_total = total

    return ContentHandle(
        content_id=content.content_id,
        url=content.url,
        source=content.source,
        title=content.title,
        content_length=len(content.extracted_text),
        preview=preview,
        find_matches=find_matches,
        find_total=find_total,
    )


def extract_text_from_html(html: str) -> str:
    """Extract plain text from HTML using trafilatura."""
    return trafilatura.extract(html) or ""


# Refetch callbacks - set by tools.py and wayback_tools.py to avoid circular imports
_refetch_direct: Callable | None = None
_refetch_wayback: Callable | None = None


def register_refetch_direct(fn: Callable) -> None:
    """Register the direct HTTP refetch function."""
    global _refetch_direct
    _refetch_direct = fn


def register_refetch_wayback(fn: Callable) -> None:
    """Register the wayback refetch function."""
    global _refetch_wayback
    _refetch_wayback = fn


async def _maybe_refetch(content_id: str) -> StoredContent | None:
    """Attempt to refetch evicted content."""
    evicted_info = _store.get_evicted_info(content_id)
    if not evicted_info:
        return None

    source = evicted_info["source"]
    url = evicted_info["url"]
    fetch_params = evicted_info.get("fetch_params") or {}

    logger.info(f"Auto-refetching evicted content {content_id} from {source}")

    if source == "direct" and _refetch_direct:
        # Refetch via direct HTTP
        await _refetch_direct(url)
    elif source == "wayback" and _refetch_wayback:
        # Refetch via wayback
        timestamp = fetch_params.get("timestamp")
        if timestamp:
            await _refetch_wayback(url, timestamp)

    # Check if it's now in the store
    return _store.get(content_id)


# LLM-facing tools


def content_search(
    content_id: Annotated[
        str,
        "The content_id from a previous fetch (tavily_extract or wayback_fetch). "
        "If the content was evicted from cache, it will be automatically re-fetched.",
    ],
    pattern: Annotated[
        str,
        """Regex pattern to search for in the content.
Examples:
- 'John Smith' (literal name)
- 'Smith|Johnson' (either name)
- '(?i)superintendent' (case-insensitive)
Use this to find specific mentions without loading full content.""",
    ],
    context_words: Annotated[
        int,
        "Number of words to show before and after each match. Default 30.",
    ] = DEFAULT_CONTEXT_WORDS,
    max_matches: Annotated[
        int,
        "Maximum matches to return. Default 10.",
    ] = DEFAULT_MAX_MATCHES,
) -> ContentSearchResult:
    """
    Search within previously fetched content using a regex pattern.

    Returns matches with surrounding word context. Use this to find specific
    names, titles, or terms without loading the entire document.

    WORKFLOW:
    1. Fetch content with tavily_extract() or wayback_fetch()
    2. Use content_search() to find relevant sections
    3. Optionally use content_read() to get more context around matches
    """
    content = _store.get(content_id)

    if not content:
        # Content not in cache - either evicted or never existed
        evicted = _store.get_evicted_info(content_id)
        if evicted:
            logger.warning(f"Content {content_id} was evicted. Re-fetch the URL: {evicted['url']}")
        else:
            logger.warning(f"Content {content_id} not found in cache or evicted list")
        # Return empty result instead of crashing - let agent handle gracefully
        return ContentSearchResult(
            content_id=content_id,
            total_matches=0,
            matches=[],
            truncated=False,
        )

    matches, total = _search_content(content.extracted_text, pattern, context_words, max_matches)

    return ContentSearchResult(
        content_id=content_id,
        total_matches=total,
        matches=matches,
        truncated=total > len(matches),
    )


def content_read(
    content_id: Annotated[
        str,
        "The content_id from a previous fetch. Auto-refetches if evicted from cache.",
    ],
    start: Annotated[
        int,
        "Character position to start reading from. Default 0 (beginning).",
    ] = 0,
    length: Annotated[
        int,
        f"Number of characters to read. Default {DEFAULT_READ_LENGTH}. Max {MAX_READ_LENGTH}.",
    ] = DEFAULT_READ_LENGTH,
) -> ContentReadResult:
    """
    Read a section of previously fetched content.

    Use this to:
    - Read the beginning of a document to understand its structure
    - Read more context around a match from content_search()
    - Paginate through a document section by section
    """
    content = _store.get(content_id)

    if not content:
        # Content not in cache - either evicted or never existed
        evicted = _store.get_evicted_info(content_id)
        if evicted:
            logger.warning(f"Content {content_id} was evicted. Re-fetch the URL: {evicted['url']}")
            msg = f"[Content evicted - please re-fetch: {evicted['url']}]"
        else:
            logger.warning(f"Content {content_id} not found in cache or evicted list")
            msg = "[Content not found - invalid content_id or already cleared]"
        # Return informative result instead of crashing
        return ContentReadResult(
            content_id=content_id,
            text=msg,
            start_offset=0,
            end_offset=0,
            total_length=0,
            has_more_before=False,
            has_more_after=False,
        )

    text = content.extracted_text
    total_length = len(text)

    # Clamp values
    start = max(0, min(start, total_length))
    length = min(length, MAX_READ_LENGTH)
    end = min(start + length, total_length)

    return ContentReadResult(
        content_id=content_id,
        text=text[start:end],
        start_offset=start,
        end_offset=end,
        total_length=total_length,
        has_more_before=start > 0,
        has_more_after=end < total_length,
    )


def content_list() -> list[ContentHandle]:
    """
    List all content currently stored from previous fetches.

    Use this to see what content is available for searching/reading.
    Returns content_id, url, title, and preview for each stored item.
    """
    handles = []
    for content in _store._store.values():
        handle = create_content_handle(content)
        handles.append(handle)
    return handles
