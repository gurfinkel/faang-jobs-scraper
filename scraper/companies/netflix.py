"""
Netflix careers scraper (wrapper + Eightfold).

Fixes:
- While sniffing JSON, we now also *click* "Show More Positions" and similar
  buttons to trigger more network calls and list expansion.
- We continue to normalize to absolute Eightfold job detail URLs.

We still keep both strategies:
  1) Network sniffing on wrapper and on Eightfold board (best source of truth).
  2) Fallback DOM scan via discover_with_playwright (including iframes).
"""
from __future__ import annotations

import json
import time
from typing import Dict, Iterable, List, Optional, Set
from urllib.parse import urljoin

from ..config import Settings
from ..io_utils import log
from ..parsing import extract_description_from_html
from ._playwright import discover_with_playwright

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError  # type: ignore
except Exception:  # pragma: no cover
    sync_playwright = None

BASE_NETFLIX_WRAPPER = "https://explore.jobs.netflix.net"
BASE_EIGHTFOLD = "https://netflix.eightfold.ai"

LIST_URLS = [
    f"{BASE_NETFLIX_WRAPPER}/careers",
    f"{BASE_EIGHTFOLD}/careers",
]

HREF_SUBSTRING = "/careers/job/"  # Eightfold detail path

def _extract_job_urls_from_json(data) -> Set[str]:
    urls: Set[str] = set()
    def rec(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str) and HREF_SUBSTRING in v:
                    v = v.strip()
                    urls.add(v if v.startswith("http") else urljoin(BASE_EIGHTFOLD, v))
            for key in ("id", "jobId", "positionId", "positionID", "position_id"):
                v = node.get(key)
                if isinstance(v, (int, str)):
                    s = str(v).strip()
                    if s.isdigit():
                        urls.add(f"{BASE_EIGHTFOLD}{HREF_SUBSTRING}{s}")
            for v in node.values():
                rec(v)
        elif isinstance(node, list):
            for it in node:
                rec(it)
    rec(data)
    return urls

def _click_more_positions(page) -> int:
    """
    Click buttons that reveal more jobs on Netflix wrapper and Eightfold.
    Returns number of clicks performed.
    """
    clicks = 0
    selectors = [
        "button.show-more-positions",              # Netflix wrapper (confirmed class)
        "button:has-text('Show More Positions')",
        "button:has-text('Show more')",
        "button:has-text('Load more')",
        "button[aria-label*='more' i]",
    ]
    for sel in selectors:
        btns = page.query_selector_all(sel)
        for btn in btns:
            try:
                if btn.is_visible() and btn.is_enabled():
                    btn.click()
                    clicks += 1
                    page.wait_for_load_state("networkidle", timeout=10_000)
                    time.sleep(0.5)
            except Exception:
                pass
    return clicks

def _discover_via_network_sniff(start_url: str, settings: Settings, max_scrolls: int = 40) -> Set[str]:
    found: Set[str] = set()
    if sync_playwright is None:
        return found

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(user_agent=settings.user_agent)
            page = context.new_page()

            def on_response(resp):
                try:
                    ctype = resp.headers.get("content-type", "") or ""
                except Exception:
                    ctype = ""
                url = resp.url
                if "application/json" not in ctype.lower():
                    return
                if ("eightfold.ai" not in url) and ("explore.jobs.netflix.net" not in url):
                    return
                data = None
                try:
                    data = resp.json()
                except Exception:
                    try:
                        data = json.loads(resp.text())
                    except Exception:
                        pass
                if data is not None:
                    urls = _extract_job_urls_from_json(data)
                    if urls:
                        found.update(urls)

            page.on("response", on_response)
            page.goto(start_url, wait_until="networkidle", timeout=60_000)

            last_height = 0
            for _ in range(max_scrolls):
                # Click "more positions" before/after scroll to trigger API calls
                _click_more_positions(page)

                try:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                except Exception:
                    pass
                time.sleep(settings.sleep_between_requests_sec)

                try:
                    height = page.evaluate("document.body.scrollHeight")
                except Exception:
                    height = last_height
                if height == last_height:
                    # Try clicking buttons again once more
                    clicked = _click_more_positions(page)
                    if clicked == 0:
                        break
                last_height = height

            # If the board is in an iframe, navigate to it and repeat short cycle
            try:
                for fr in page.frames:
                    fr_url = getattr(fr, "url", "")
                    if "eightfold.ai" in fr_url and "careers" in fr_url:
                        page.goto(fr_url, wait_until="networkidle", timeout=60_000)
                        for _ in range(15):
                            _click_more_positions(page)
                            try:
                                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                            except Exception:
                                pass
                            time.sleep(settings.sleep_between_requests_sec)
                        break
            except Exception:
                pass

            context.close()
            browser.close()
    except Exception as e:
        log(settings, f"playwright error during Netflix network sniff: {e}")
    return found

def discover(session, settings: Settings) -> List[str]:
    urls: Set[str] = set()

    # 1) Prefer network sniff on both URLs
    for u in LIST_URLS:
        urls |= _discover_via_network_sniff(u, settings, max_scrolls=40)

    # 2) Fallback: anchor scanning (page + iframes) in case JSON parsing missed some
    if not urls:
        anchor_urls = discover_with_playwright(
            list_url=LIST_URLS[0],
            href_substring=HREF_SUBSTRING,
            base=BASE_NETFLIX_WRAPPER,
            settings=settings,
            max_scrolls=40,
            extra_list_urls=[LIST_URLS[1]],
            scan_iframes=True,
        )
        urls |= set(anchor_urls)

    # Normalize to absolute Eightfold job detail URLs
    normalized: List[str] = []
    for url in urls:
        normalized.append(url if url.startswith("http") else urljoin(BASE_EIGHTFOLD, url))

    unique = sorted(set(normalized))
    log(settings, f"netflix: discovered {len(unique)} URLs")
    return unique

def get_description(url: str, settings: Settings) -> Optional[str]:
    if sync_playwright is None:
        log(settings, "Playwright not installed; cannot fetch Netflix descriptions.")
        return None

    html: Optional[str] = None
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(user_agent=settings.user_agent)
            page = context.new_page()

            page.goto(url, wait_until="networkidle", timeout=60_000)
            try:
                page.wait_for_selector(
                    "main, article, [data-testid='job-details'], [data-testid='job-description'], .position-description, .job-description",
                    timeout=10_000,
                )
            except PlaywrightTimeoutError:
                pass

            html = page.content()
            context.close()
            browser.close()
    except Exception as e:
        log(settings, f"playwright error on Netflix detail: {e}")
        return None

    return extract_description_from_html(html) if html else None

def get_descriptions_batch(urls: Iterable[str], settings: Settings) -> Dict[str, str]:
    descriptions: Dict[str, str] = {}
    for url in urls:
        try:
            desc = get_description(url, settings)
            if desc:
                descriptions[url] = desc
        except Exception as e:
            log(settings, f"netflix: error fetching {url}: {e}")
        time.sleep(settings.sleep_between_requests_sec)
    return descriptions
