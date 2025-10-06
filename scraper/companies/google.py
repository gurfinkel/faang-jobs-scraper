"""Scraper for Google's careers site.

This module uses Playwright to handle Google's client‑side rendered
job listings.  The Google careers site dynamically loads job cards
via JavaScript, so a simple HTTP GET will not reveal all jobs.  To
extract job posting links, we launch a headless Chromium browser,
navigate to the jobs results page, and scroll the page to trigger
lazy loading.  Links are collected from the DOM after each scroll.

If Playwright is not installed, the module logs a message and
returns an empty list.
"""

from __future__ import annotations

import time
from typing import List, Set
from urllib.parse import urljoin

from ..config import Settings
from ..io_utils import log

BASE = "https://careers.google.com"

try:
    # Try to import Playwright.  If unavailable, discover will no‑op.
    from playwright.sync_api import sync_playwright  # type: ignore
except Exception:  # pragma: no cover - Playwright may not be installed
    sync_playwright = None  # type: ignore


def _collect_job_links(page) -> Set[str]:
    """Return a set of absolute job detail URLs currently present on the page.

    Google job cards are represented by anchors whose ``href`` contains
    ``/jobs/results/``.  Both absolute and relative hrefs are handled.

    Args:
        page: A Playwright page object with a loaded jobs results page.

    Returns:
        A set of absolute job detail URLs.
    """
    urls: Set[str] = set()
    anchors = page.query_selector_all("a[href*='/jobs/results/']")
    for a in anchors:
        href = a.get_attribute("href")
        if not href:
            continue
        href = href.strip()
        if href.startswith("http"):
            urls.add(href)
        else:
            urls.add(urljoin(BASE, href))
    return urls


def discover(session, settings: Settings) -> List[str]:
    """Discover Google job posting URLs using Playwright.

    This function navigates to the Google careers search results page
    and scrolls the page repeatedly to trigger additional jobs to
    load.  After each scroll, it collects job links and stops when
    either the maximum number of scrolls (from ``settings.max_pages``)
    is reached or no new content loads.

    Args:
        session: Ignored; included for API compatibility.
        settings: Configuration controlling user agent, timeouts and
            the maximum number of scrolls.

    Returns:
        A sorted list of unique job posting URLs.  If Playwright is
        unavailable, an empty list is returned.
    """
    urls: Set[str] = set()

    # Abort if Playwright isn't available
    if sync_playwright is None:
        log(settings, "google: Playwright not installed; cannot discover jobs")
        return []

    # Determine how many scrolls to perform.  ``max_pages`` maps to
    # scroll operations; fall back to 40 if unset or invalid.
    max_scrolls = settings.max_pages
    if not isinstance(max_scrolls, int) or max_scrolls <= 0:
        max_scrolls = 40

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(user_agent=settings.user_agent)
            page = context.new_page()
            # Navigate to the jobs results page
            page.goto(f"{BASE}/jobs/results/", wait_until="networkidle", timeout=60_000)

            # Collect links before scrolling
            urls |= _collect_job_links(page)
            last_height = 0
            scroll_count = 0

            while scroll_count < max_scrolls:
                # Scroll to bottom to load more jobs
                try:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                except Exception:
                    pass
                time.sleep(settings.sleep_between_requests_sec)
                urls |= _collect_job_links(page)
                # Check if new content loaded based on scroll height
                try:
                    height = page.evaluate("document.body.scrollHeight")
                except Exception:
                    height = last_height
                if height == last_height:
                    break
                last_height = height
                scroll_count += 1

            context.close()
            browser.close()
    except Exception as e:
        log(settings, f"google: Playwright error during discovery: {e}")

    sorted_urls = sorted(urls)
    log(settings, f"google: discovered {len(sorted_urls)} URLs")
    return sorted_urls
