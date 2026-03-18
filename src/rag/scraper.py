"""Web scraper: extracts text + alt text, strips nav/footer.

Note: robots.txt checking is deferred to Phase 2+.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Tags to remove before extraction
_STRIP_TAGS = {"nav", "footer", "header", "script", "style", "noscript", "aside"}


@dataclass(frozen=True)
class ScrapedPage:
    """Immutable scraped page with extracted text and raw HTML."""

    url: str
    title: str
    text: str
    raw_html: str


def scrape_page(url: str, *, timeout: float = 30.0) -> ScrapedPage:
    """Scrape a single page, extracting text and alt text from images.

    Strips nav, header, footer, script, and style tags before extraction.

    Args:
        url: The URL to scrape.
        timeout: HTTP request timeout in seconds.

    Returns:
        ScrapedPage with clean text, title, URL, and raw HTML.

    Raises:
        httpx.HTTPStatusError: On non-2xx responses.
    """
    response = httpx.get(url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()

    raw_html = response.text
    soup = BeautifulSoup(raw_html, "lxml")

    # Remove unwanted tags
    for tag_name in _STRIP_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # Extract title
    title = soup.title.string.strip() if soup.title and soup.title.string else ""

    # Extract text + alt text from images
    parts: list[str] = []
    for element in soup.find_all(
        [
            "p",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "li",
            "td",
            "th",
            "blockquote",
            "img",
        ]
    ):
        if element.name == "img":
            alt = element.get("alt", "")
            if alt:
                parts.append(f"[Image: {alt}]")
        else:
            text = element.get_text(separator=" ", strip=True)
            if text:
                parts.append(text)

    clean_text = "\n".join(parts)
    # Collapse multiple blank lines
    clean_text = re.sub(r"\n{3,}", "\n\n", clean_text)

    return ScrapedPage(
        url=str(response.url),
        title=title,
        text=clean_text,
        raw_html=raw_html,
    )


def scrape_site(
    start_url: str,
    *,
    max_pages: int = 50,
    timeout: float = 30.0,
) -> list[ScrapedPage]:
    """Crawl a site starting from start_url, following same-domain links.

    Args:
        start_url: The starting URL to crawl.
        max_pages: Maximum number of pages to scrape.
        timeout: HTTP request timeout per page.

    Returns:
        List of ScrapedPage objects, one per successfully scraped page.
    """
    parsed_start = urlparse(start_url)
    domain = parsed_start.netloc
    visited: set[str] = set()
    to_visit: list[str] = [start_url]
    pages: list[ScrapedPage] = []

    while to_visit and len(pages) < max_pages:
        url = to_visit.pop(0)
        normalized = _normalize_url(url)

        if normalized in visited:
            continue
        visited.add(normalized)

        try:
            page = scrape_page(url, timeout=timeout)
            pages.append(page)
            logger.info("Scraped %s (%d chars)", url, len(page.text))

            # Extract same-domain links
            soup = BeautifulSoup(page.raw_html, "lxml")
            for link in soup.find_all("a", href=True):
                href_val = str(link["href"])
                full_url = urljoin(url, href_val)
                parsed = urlparse(full_url)
                if parsed.netloc == domain and _normalize_url(full_url) not in visited:
                    to_visit.append(full_url)

        except Exception as exc:
            logger.warning("Failed to scrape %s: %s", url, exc)

    return pages


def _normalize_url(url: str) -> str:
    """Normalize URL by removing fragment and trailing slash."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme}://{parsed.netloc}{path}"
