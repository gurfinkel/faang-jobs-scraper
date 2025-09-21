# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Iterable, List, Set
from urllib.parse import urljoin
import time

from ..config import Settings
from ..io_utils import log

def _collect_links_from_context(ctx, patterns: Iterable[str], base_url: str) -> Set[str]:
    """Collect hrefs from a Page or Frame for any anchors whose href contains one of the patterns."""
    urls: Set[str] = set()
    try:
        anchors = ctx.locator("a[href]")
        count = anchors.count()
        for idx in range(count):
            try:
                href = anchors.nth(idx).get_attribute("href") or ""
                if not href:
                    continue
                if any(p in href for p in patterns):
                    # Use the context URL (page/frame) to resolve relative links; fallback to base_url
                    ctx_url = getattr(ctx, "url", None) or base_url
                    abs_url = urljoin(ctx_url, href)
                    urls.add(abs_url)
            except Exception:
                pass
    except Exception:
        pass
    return urls

def discover_with_playwright(
    list_url: str,
    href_substring: str,
    base: str,
    settings: Settings,
    max_scrolls: int = 20,
    # NEW: optional extra URLs we will try if the first page yields too few links
    extra_list_urls: List[str] | None = None,
    # NEW: also scan inside iframes (Eightfold embeds)
    scan_iframes: bool = True,
) -> List[str]:
    """
    Generic helper to capture job detail links from an infinite-scroll or JS-rendered listing page.
    Returns a sorted list of absolute URLs whose href contains `href_substring` (and extra patterns).
    """
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        log(settings, f"playwright not installed; skipping JS-rendered site: {list_url}")
        return []

    patterns = [href_substring]
    urls: Set[str] = set()

    def crawl_one(url: str) -> Set[str]:
        local_urls: Set[str] = set()
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                context = browser.new_context(user_agent=settings.user_agent)
                page = context.new_page()
                log(settings, f"navigating {url}")
                page.goto(url, wait_until="networkidle", timeout=60_000)

                last_height = 0
                for _ in range(max_scrolls):
                    # Collect from the main page
                    local_urls |= _collect_links_from_context(page, patterns, base)

                    # Optionally collect from iframes (e.g., Eightfold embed)
                    if scan_iframes:
                        try:
                            for fr in page.frames:
                                local_urls |= _collect_links_from_context(fr, patterns, base)
                        except Exception:
                            pass

                    # Try to scroll both page and frames to trigger lazy loading
                    try:
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    except Exception:
                        pass
                    if scan_iframes:
                        for fr in page.frames:
                            try:
                                fr.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                            except Exception:
                                pass

                    time.sleep(1.0)

                    # crude bottom detection on main page
                    try:
                        height = page.evaluate("document.body.scrollHeight")
                    except Exception:
                        height = 0
                    if height == last_height:
                        break
                    last_height = height

                context.close()
                browser.close()
        except Exception as e:
            log(settings, f"playwright error while crawling {url}: {e}")
        return local_urls

    # 1) Try the primary list_url first
    urls |= crawl_one(list_url)

    # 2) If we saw very few/no links, try any extra listing URLs
    if (not urls or len(urls) < 5) and extra_list_urls:
        for u in extra_list_urls:
            urls |= crawl_one(u)

    return sorted(urls)
