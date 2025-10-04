from typing import List, Optional
from ..config import Settings
from ..io_utils import log
from ..parsing import extract_description_from_html
from ._playwright import discover_with_playwright

BASE_URL = "https://www.metacareers.com"
LIST_URL = f"{BASE_URL}/jobs"
HREF_SUBSTRING = "/jobs/"

def discover(session, settings: Settings) -> List[str]:
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
    """
    Fetch Meta job detail HTML with Playwright (no login). This avoids 400s that 'requests' hits.
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

            # If content is still skeletal, give it a brief nudge and retry once
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
