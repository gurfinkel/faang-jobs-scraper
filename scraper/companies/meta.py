"""
Meta careers scraper.

Fixes:
- Actively clicks "See more / Load more" buttons while scrolling.
- Collects only job detail URLs: https://www.metacareers.com/jobs/<NUMERIC_ID>/
"""
from __future__ import annotations

import re
import time
from typing import List, Set
from urllib.parse import urljoin, urlparse

from ..config import Settings
from ..io_utils import log
from ..parsing import extract_description_from_html

BASE_URL = "https://www.metacareers.com"
LIST_URL = f"{BASE_URL}/jobs"
# Detail pages are /jobs/<digits>/ (optional trailing slash)
_DETAIL_RE = re.compile(r"^/jobs/\d+/?$", re.I)

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError  # type: ignore
except Exception:  # pragma: no cover
    sync_playwright = None  # type: ignore

def _is_detail(href: str) -> bool:
    try:
        p = urlparse(href)
    except Exception:
        return False
    if p.netloc and p.netloc != "www.metacareers.com":
        return False
    return bool(_DETAIL_RE.match(p.path or ""))

def _collect_job_links(page) -> Set[str]:
    urls: Set[str] = set()
    for a in page.query_selector_all("a[href*='/jobs/']"):
        href = (a.get_attribute("href") or "").strip()
        if not href:
            continue
        abs_url = href if href.startswith("http") else urljoin(BASE_URL, href)
        if _is_detail(abs_url):
            urls.add(abs_url.rstrip("/"))
    return urls

def _click_load_more_if_any(page) -> int:
    clicks = 0
    selectors = [
        "button:has-text('Load more')",
        "button:has-text('See more')",
        "button:has-text('Show more')",
        "button[aria-label*='more' i]",
    ]
    for sel in selectors:
        btns = page.query_selector_all(sel)
        for btn in btns:
            try:
                if btn.is_enabled() and btn.is_visible():
                    btn.click()
                    clicks += 1
                    page.wait_for_load_state("networkidle", timeout=10_000)
                    time.sleep(0.5)
            except Exception:
                pass
    return clicks

def discover(session, settings: Settings) -> List[str]:
    urls: Set[str] = set()
    if sync_playwright is None:
        log(settings, "Meta: Playwright not installed; cannot discover jobs.")
        return []

    max_scrolls = settings.max_pages if isinstance(settings.max_pages, int) and settings.max_pages > 0 else 30

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(user_agent=settings.user_agent)
            page = context.new_page()

            page.goto(LIST_URL, wait_until="networkidle", timeout=60_000)

            last_count = -1
            for _ in range(max_scrolls):
                # Scroll to bottom to reveal lazy loads/buttons
                try:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                except Exception:
                    pass
                time.sleep(settings.sleep_between_requests_sec)

                # Click any "load more"/"see more" style button
                _click_load_more_if_any(page)

                # Collect links
                urls |= _collect_job_links(page)
                if len(urls) == last_count:
                    break
                last_count = len(urls)

            context.close()
            browser.close()
    except Exception as e:
        log(settings, f"meta: Playwright error during discovery: {e}")

    result = sorted(urls)
    log(settings, f"meta: discovered {len(result)} URLs")
    return result

def get_description(url: str, settings: Settings) -> str | None:
    if sync_playwright is None:
        log(settings, "Playwright not installed; cannot fetch Meta descriptions.")
        return None
    html = None
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(user_agent=settings.user_agent)
            page = context.new_page()
            page.goto(url, wait_until="networkidle", timeout=60_000)

            try:
                page.wait_for_load_state("domcontentloaded", timeout=10_000)
            except PlaywrightTimeoutError:
                pass

            html = page.content()
            context.close()
            browser.close()
    except Exception as e:
        log(settings, f"Playwright error on Meta detail: {e}")
        return None

    return extract_description_from_html(html) if html else None
