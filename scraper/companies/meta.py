"""Scraper for Meta (Facebook) careers site.

This module uses Playwright via the helper ``discover_with_playwright``
function to scroll through the Meta careers board, gather job detail
links and optionally fetch job descriptions.  Meta's site is
heavily client‑rendered, so Playwright is required to load the
content.  The discovery function returns a list of unique job URLs.

Functions
---------
discover(session, settings)
    Return a list of job posting URLs on Meta's careers board.

get_description(url, settings)
    Fetch and return the job description HTML for a single job URL using
    Playwright.

get_descriptions_batch(urls, settings)
    Fetch descriptions for multiple URLs sequentially, returning a
    mapping of URL to description.  This helper is useful when
    scraping job details in bulk and avoids repeated Playwright imports
    in the caller.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

from ..config import Settings
from ..io_utils import log
from ..parsing import extract_description_from_html
from ._playwright import discover_with_playwright


# Base and list URLs for Meta careers.  ``LIST_URL`` points to the job
# board listing.  ``HREF_SUBSTRING`` is used to filter anchor hrefs that
# correspond to job detail pages.
BASE_URL = "https://www.metacareers.com"
LIST_URL = f"{BASE_URL}/jobs"
HREF_SUBSTRING = "/jobs/"


def discover(session, settings: Settings) -> List[str]:
    """Discover Meta job posting URLs using Playwright.

    This function delegates to :func:`discover_with_playwright` with
    sensible defaults to scroll and extract job links.  The
    ``max_scrolls`` parameter controls how many times the page will
    be scrolled to load additional jobs.  Links are deduplicated and
    logged before returning.

    Args:
        session: Ignored for Meta scraping; kept for interface
            compatibility with other scrapers.
        settings: A Settings instance controlling user‑agent and
            wait times.

    Returns:
        A list of unique job URLs discovered on the Meta careers site.
    """
    # Use a helper from the _playwright module.  It handles
    # headless browser setup and anchor scanning.
    urls = discover_with_playwright(
        list_url=LIST_URL,
        href_substring=HREF_SUBSTRING,
        base=BASE_URL,
        settings=settings,
        max_scrolls=20,
    )
    log(settings, f"meta: discovered {len(urls)} URLs")
    return urls


def get_description(url: str, settings: Settings) -> Optional[str]:
    """Fetch a Meta job detail page and extract its description.

    This function uses Playwright to navigate to the given URL.  It
    waits for the page to be fully loaded, then returns the HTML
    description extracted via :func:`extract_description_from_html`.

    Args:
        url: A fully qualified URL pointing to a Meta job detail page.
        settings: A Settings instance controlling the user agent and
            timeouts.

    Returns:
        A description string if successful, otherwise ``None``.
    """
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        log(settings, "Playwright not installed; cannot fetch Meta descriptions.")
        return None

    html: Optional[str] = None
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(user_agent=settings.user_agent)
            page = context.new_page()
            # Load the page like a human browser would
            page.goto(url, wait_until="networkidle", timeout=60_000)

            # If content is still skeletal, give it a brief nudge and retry
            content = page.content()
            if len(content or "") < 2000:
                page.wait_for_load_state("domcontentloaded", timeout=10_000)
                content = page.content()

            html = content
            context.close()
            browser.close()
    except Exception as e:
        log(settings, f"Playwright error on Meta detail: {e}")
        return None

    return extract_description_from_html(html) if html else None


def get_descriptions_batch(urls: List[str], settings: Settings) -> Dict[str, str]:
    """Fetch multiple Meta job descriptions in a batch.

    This helper loops over each URL, calls :func:`get_description` and
    collates the results into a mapping.  Any exceptions are logged
    and skipped.  A brief pause is inserted between requests to
    respect rate limits and reduce the load on the target site.

    Args:
        urls: An iterable of Meta job detail URLs.
        settings: A Settings instance controlling the user agent and
            sleep intervals.

    Returns:
        A dictionary mapping each URL to its extracted description.  URLs
        that fail to fetch are omitted from the result.
    """
    descriptions: Dict[str, str] = {}
    for url in urls:
        try:
            desc = get_description(url, settings)
            if desc:
                descriptions[url] = desc
        except Exception as e:
            log(settings, f"meta: error fetching {url}: {e}")
        # Respect polite crawling delays
        time.sleep(settings.sleep_between_requests_sec)
    return descriptions
