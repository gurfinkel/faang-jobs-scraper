"""
Google Careers scraper using Playwright.

Fixes:
- Collects ONLY job detail URLs (with numeric ID segment).
- Exhausts the in-page results by scrolling AND clicking "load more" buttons.
- Also visits paginated results via ?page=2..N to catch any server-side pages.

Detail URL examples: /jobs/results/73675063508771526-software-engineer-iii/
Google also exposes a page parameter: /jobs/results/?page=30
(Confirmed via public pages and job feeds.)
"""
from __future__ import annotations

import re
import time
from typing import List, Set
from urllib.parse import urljoin, urlparse

from ..config import Settings
from ..io_utils import log

BASE = "https://careers.google.com"

try:
    from playwright.sync_api import sync_playwright  # type: ignore
except Exception:  # pragma: no cover
    sync_playwright = None  # type: ignore

_DETAIL_RE = re.compile(r"^/jobs/results/\d{6,}(-[a-z0-9-]+)?/?$", re.I)

def _is_job_detail_url(href: str) -> bool:
    try:
        p = urlparse(href)
    except Exception:
        return False
    if p.netloc and p.netloc != "careers.google.com":
        return False
    return bool(_DETAIL_RE.match(p.path or ""))

def _collect_job_links(page) -> Set[str]:
    urls: Set[str] = set()
    anchors = page.query_selector_all("a[href*='/jobs/results/']")
    for a in anchors:
        href = (a.get_attribute("href") or "").strip()
        if not href:
            continue
        abs_url = href if href.startswith("http") else urljoin(BASE, href)
        if _is_job_detail_url(abs_url):
            urls.add(abs_url)
    return urls

def _click_load_more_if_any(page) -> int:
    """
    Click common 'load more' buttons if found; return number of clicks performed.
    """
    selectors = [
        "button[aria-label*='more' i]",
        "button:has-text('Load more')",
        "button:has-text('More jobs')",
        "button:has-text('Show more')",
        "button:has-text('More results')",
        "div[role='button']:has-text('Load more')",
    ]
    clicks = 0
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

def _exhaust_results_on_page(page, settings: Settings, max_scrolls: int) -> Set[str]:
    """
    Scroll + click-load-more loop until content plateaus.
    """
    urls: Set[str] = set()
    last_count = -1
    scrolls = 0
    while scrolls < max_scrolls:
        # Scroll to bottom
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass
        time.sleep(settings.sleep_between_requests_sec)

        # Click any 'load more' if present
        _click_load_more_if_any(page)

        # Collect links
        urls |= _collect_job_links(page)

        # Plateau check
        if len(urls) == last_count:
            break
        last_count = len(urls)
        scrolls += 1
    return urls

def discover(session, settings: Settings) -> List[str]:
    """
    Discover Google job posting URLs using Playwright.

    Strategy:
      1) Visit /jobs/results/ and exhaust it by scrolling and clicking 'load more'.
      2) Then, visit explicit pages /jobs/results/?page=2..N as a safety net.
    """
    urls: Set[str] = set()

    if sync_playwright is None:
        log(settings, "google: Playwright not installed; cannot discover jobs")
        return []

    max_scrolls = settings.max_pages if isinstance(settings.max_pages, int) and settings.max_pages > 0 else 40
    max_pages = max_scrolls  # reuse the same cap to avoid a new setting

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(user_agent=settings.user_agent)
            page = context.new_page()

            # 1) Main results page
            page.goto(f"{BASE}/jobs/results/", wait_until="networkidle", timeout=60_000)
            urls |= _exhaust_results_on_page(page, settings, max_scrolls=max_scrolls)

            # 2) Explicit pagination fallback (?page=2..N)
            for i in range(2, max_pages + 1):
                try:
                    page.goto(f"{BASE}/jobs/results/?page={i}", wait_until="networkidle", timeout=60_000)
                    new_urls = _exhaust_results_on_page(page, settings, max_scrolls=10)
                    if not new_urls:
                        break
                    before = len(urls)
                    urls |= new_urls
                    if len(urls) == before:
                        # No growth – likely exhausted
                        break
                except Exception:
                    break

            context.close()
            browser.close()
    except Exception as e:
        log(settings, f"google: Playwright error during discovery: {e}")

    sorted_urls = sorted(urls)
    log(settings, f"google: discovered {len(sorted_urls)} URLs")
    return sorted_urls
