"""Scraper for Netflix careers pages.

Netflix hosts its job board via Eightfold, which delivers job data
through JSON endpoints.  This module uses a combination of Playwright
network sniffing and generic link scanning to extract job detail
URLs.  The network sniffing approach listens for JSON responses
containing job IDs and constructs the corresponding Eightfold job
detail URLs.  As a fallback, the helper
``discover_with_playwright`` is used to scan anchors from the
Netflix wrapper pages and any embedded iframes.

After discovering job URLs, a separate :func:`get_description`
function is provided to fetch the description for an individual job
using Playwright.  For convenience, :func:`get_descriptions_batch`
performs this action over an iterable of URLs and returns a mapping
from URL to description.
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
except Exception:  # pragma: no cover - Playwright may not be installed
    sync_playwright = None


BASE_NETFLIX_WRAPPER = "https://explore.jobs.netflix.net"
BASE_EIGHTFOLD = "https://netflix.eightfold.ai"

# URLs that embed the job board.  The first is the Netflix wrapper,
# the second is the Eightfold careers board.  Both are visited by
# ``discover``.
LIST_URLS = [
    f"{BASE_NETFLIX_WRAPPER}/careers",
    f"{BASE_EIGHTFOLD}/careers",
]

HREF_SUBSTRING = "/careers/job/"


def _extract_job_urls_from_json(data) -> Set[str]:
    """Recursively extract job detail URLs from a parsed JSON object.

    The Eightfold API returns JSON structures that contain job IDs in
    various fields.  This helper walks the entire object looking for
    substrings that contain ``HREF_SUBSTRING`` or numeric IDs that
    can be turned into job URLs.

    Args:
        data: Parsed JSON (dict or list).

    Returns:
        A set of absolute Eightfold job detail URLs.
    """
    urls: Set[str] = set()

    def rec(node):
        if isinstance(node, dict):
            # Direct URL fields
            for k, v in node.items():
                if isinstance(v, str) and HREF_SUBSTRING in v:
                    v = v.strip()
                    if v.startswith("http"):
                        urls.add(v)
                    else:
                        urls.add(urljoin(BASE_EIGHTFOLD, v))
            # IDs we can turn into /careers/job/<id>
            for key in ("id", "jobId", "positionId", "positionID", "position_id"):
                v = node.get(key)
                if isinstance(v, (int, str)):
                    s = str(v).strip()
                    if s.isdigit():
                        urls.add(f"{BASE_EIGHTFOLD}{HREF_SUBSTRING}{s}")
            # Recurse into nested fields
            for v in node.values():
                rec(v)
        elif isinstance(node, list):
            for item in node:
                rec(item)

    rec(data)
    return urls


def _discover_via_network_sniff(start_url: str, settings: Settings, max_scrolls: int = 40) -> Set[str]:
    """Open a page with Playwright and capture JSON responses to extract job URLs.

    This method listens to all network responses on the given page and
    attempts to parse JSON bodies for job IDs or direct job URLs.  It
    scrolls the page several times to trigger additional network
    requests and returns all unique URLs discovered.

    Args:
        start_url: The URL to navigate to for sniffing.
        settings: A Settings instance controlling user agent and sleep
            durations.
        max_scrolls: Number of scrolls to perform to trigger network
            requests.  Increase if the site loads content lazily.

    Returns:
        A set of absolute Eightfold job detail URLs discovered via
        network sniffing.
    """
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
                    ctype = resp.headers.get("content-type", "")
                except Exception:
                    ctype = ""
                url = resp.url
                if "application/json" not in ctype:
                    return
                if ("eightfold.ai" not in url) and ("explore.jobs.netflix.net" not in url):
                    return
                # Attempt to parse JSON; handle both text and .json() APIs
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

            # Scroll to trigger lazy loads and network calls
            last_height = 0
            for _ in range(max_scrolls):
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
                    break
                last_height = height

            # If an iframe hosts the job board, navigate directly to its URL once
            try:
                for fr in page.frames:
                    fr_url = getattr(fr, "url", "")
                    if "eightfold.ai" in fr_url and "careers" in fr_url:
                        page.goto(fr_url, wait_until="networkidle", timeout=60_000)
                        for _ in range(max_scrolls):
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
    """Discover Netflix job posting URLs.

    The discovery process uses two strategies: first, network sniffing
    to capture JSON responses that contain job IDs and assemble job
    URLs; second, as a fallback, scanning anchors via
    :func:`discover_with_playwright` on the wrapper page and any
    embedded iframes.  All discovered URLs are normalized to absolute
    Eightfold job detail URLs.

    Args:
        session: Ignored for Netflix scraping; included for interface
            compatibility.
        settings: A Settings instance controlling user agent and
            sleep durations.

    Returns:
        A sorted list of unique job posting URLs.
    """
    urls: Set[str] = set()

    # 1) Prefer network sniff (most reliable on Eightfold embeds)
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
        if url.startswith("http"):
            normalized.append(url)
        else:
            normalized.append(urljoin(BASE_EIGHTFOLD, url))

    unique = sorted(set(normalized))
    log(settings, f"netflix: discovered {len(unique)} URLs")
    return unique


def get_description(url: str, settings: Settings) -> Optional[str]:
    """Fetch a Netflix job detail page via Playwright.

    The Eightfold job pages are client‑rendered and require
    Playwright to fetch.  This function loads the page, waits for
    common selectors that contain job descriptions and returns the
    extracted text via :func:`extract_description_from_html`.

    Args:
        url: A fully qualified Eightfold job detail URL.
        settings: A Settings instance controlling user agent and
            timeouts.

    Returns:
        A description string if successful, otherwise ``None``.
    """
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
            # Wait for typical description containers, but don't fail if not found
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
    """Fetch multiple Netflix job descriptions in a batch.

    This helper iterates over the given URLs, invokes
    :func:`get_description` for each and collects the results into a
    dictionary.  Any exceptions are logged and the corresponding
    entries are omitted.  A short pause is inserted between
    requests to reduce load on the target servers.

    Args:
        urls: An iterable of Eightfold job detail URLs.
        settings: A Settings instance controlling the user agent and
            sleep intervals.

    Returns:
        A mapping from each successfully fetched URL to its job
        description.
    """
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
