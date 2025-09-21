# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import time
from typing import List, Optional, Set
from urllib.parse import urljoin

from ..config import Settings
from ..io_utils import log
from ..parsing import extract_description_from_html
from ._playwright import discover_with_playwright

BASE_NETFLIX_WRAPPER = "https://explore.jobs.netflix.net"
BASE_EIGHTFOLD = "https://netflix.eightfold.ai"

LIST_URLS = [
    f"{BASE_NETFLIX_WRAPPER}/careers",
    f"{BASE_EIGHTFOLD}/careers",
]

HREF_SUBSTRING = "/careers/job/"

def _extract_job_urls_from_json(data) -> Set[str]:
    """Recursively pull job detail URLs or build them from IDs in Eightfold JSON blobs."""
    urls: Set[str] = set()

    def rec(node):
        if isinstance(node, dict):
            # Direct URL fields
            for k, v in node.items():
                if isinstance(v, str):
                    if HREF_SUBSTRING in v:
                        # Absolute or relative URL
                        if v.startswith("http"):
                            urls.add(v)
                        else:
                            urls.add(urljoin(BASE_EIGHTFOLD, v))
                # IDs we can turn into /careers/job/<id>
            # Common id keys seen in Eightfold payloads
            for key in ("id", "jobId", "positionId", "positionID", "position_id"):
                v = node.get(key)
                if isinstance(v, (int, str)):
                    s = str(v).strip()
                    if s.isdigit():
                        urls.add(f"{BASE_EIGHTFOLD}{HREF_SUBSTRING}{s}")
            # Recurse
            for v in node.values():
                rec(v)
        elif isinstance(node, list):
            for item in node:
                rec(item)

    rec(data)
    return urls

def _discover_via_network_sniff(start_url: str, settings: Settings, max_scrolls: int = 40) -> Set[str]:
    """Open start_url with Playwright, capture JSON responses, and extract job URLs."""
    found: Set[str] = set()
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        log(settings, "playwright not installed; skipping network sniff for Netflix")
        return found

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(user_agent=settings.user_agent)
            page = context.new_page()

            def on_response(resp):
                try:
                    ctype = resp.headers.get("content-type", "")
                except Exception:
                    ctype = ""
                # Only JSON from netflix Eightfold or the wrapper
                url = resp.url
                if "application/json" not in ctype:
                    return
                if ("eightfold.ai" not in url) and ("explore.jobs.netflix.net" not in url):
                    return
                try:
                    data = resp.json()
                except Exception:
                    try:
                        data = json.loads(resp.text())
                    except Exception:
                        return
                urls = _extract_job_urls_from_json(data)
                if urls:
                    found.update(urls)

            page.on("response", on_response)

            log(settings, f"navigating {start_url}")
            page.goto(start_url, wait_until="networkidle", timeout=60_000)

            # Scroll to trigger lazy loads (and frame loads, if any)
            last_height = 0
            for _ in range(max_scrolls):
                try:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                except Exception:
                    pass
                time.sleep(1.0)
                try:
                    height = page.evaluate("document.body.scrollHeight")
                except Exception:
                    height = 0
                if height == last_height:
                    break
                last_height = height

            # If an iframe hosts the job board, navigate directly to its URL once
            try:
                for fr in page.frames:
                    fr_url = getattr(fr, "url", "")
                    if "eightfold.ai" in fr_url and "careers" in fr_url:
                        log(settings, f"navigating embedded board {fr_url}")
                        page.goto(fr_url, wait_until="networkidle", timeout=60_000)
                        for _ in range(30):
                            try:
                                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                            except Exception:
                                pass
                            time.sleep(1.0)
                        break
            except Exception:
                pass

            context.close()
            browser.close()
    except Exception as e:
        log(settings, f"Playwright error during Netflix network sniff: {e}")

    return found

def discover(session, settings: Settings) -> List[str]:
    # 1) Prefer network sniff (most reliable on Eightfold embeds)
    urls: Set[str] = set()
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
    normalized = []
    for url in urls:
        if url.startswith("http"):
            normalized.append(url)
        else:
            normalized.append(urljoin(BASE_EIGHTFOLD, url))

    unique = sorted(set(normalized))
    log(settings, f"netflix: discovered {len(unique)} URLs")
    return unique

def get_description(url: str, settings: Settings) -> Optional[str]:
    """Fetch Netflix job detail HTML with Playwright (no login needed)."""
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError  # type: ignore
    except ImportError:
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
                    timeout=10_000
                )
            except PlaywrightTimeoutError:
                pass

            html = page.content()
            context.close()
            browser.close()
    except Exception as e:
        log(settings, f"Playwright error on Netflix detail: {e}")
        return None

    return extract_description_from_html(html) if html else None
