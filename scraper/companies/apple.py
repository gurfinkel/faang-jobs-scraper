from typing import List, Optional
from ..config import Settings
from ..io_utils import log
from ..parsing import extract_description_from_html
from playwright.sync_api import sync_playwright

BASE_URL = "https://jobs.apple.com"
LIST_URL = f"{BASE_URL}/en-us/search"
HREF_SUBSTRING = "/details/"

def discover(session, settings: Settings, max_pages: int = 10) -> List[str]:
    """
    Return a list of job detail URLs from Apple’s search.  Iterates through paginated
    results rather than stopping after the first page.  Limits to max_pages to avoid
    scraping all 170+ pages in one run.
    """
    urls: set[str] = set()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(user_agent=settings.user_agent)
            page = context.new_page()
            page.goto(LIST_URL, wait_until="networkidle", timeout=60_000)

            for page_idx in range(max_pages):
                # wait for job cards to render and collect all job links on this page
                page.wait_for_selector(f"a[href*='{HREF_SUBSTRING}']", timeout=30_000)
                anchors = page.query_selector_all(f"a[href*='{HREF_SUBSTRING}']")
                for a in anchors:
                    href = a.get_attribute("href")
                    if href:
                        if href.startswith("http"):
                            urls.add(href)
                        else:
                            urls.add(f"{BASE_URL}{href}")

                # look for a Next Page button that isn’t disabled; if none, break
                next_btn = page.query_selector("button[aria-label='Next Page']:not([disabled])")
                if not next_btn:
                    break
                # click Next Page and wait for new results
                next_btn.click()
                page.wait_for_load_state("networkidle", timeout=60_000)

            context.close()
            browser.close()
    except Exception as e:
        log(settings, f"apple: Playwright error during pagination: {e}")

    # Return sorted list of unique job URLs
    sorted_urls = sorted(urls)
    log(settings, f"apple: discovered {len(sorted_urls)} URLs across {max_pages} pages")
    return sorted_urls

def get_description(url: str, settings: Settings) -> Optional[str]:
    """
    Fetch an Apple job detail page via Playwright when requests fails or
    the page is heavily scripted.
    """
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        log(settings, "Playwright not installed; cannot fetch Apple descriptions.")
        return None

    html: Optional[str] = None
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(user_agent=settings.user_agent)
            page = context.new_page()
            page.goto(url, wait_until="networkidle", timeout=60_000)
            content = page.content()
            if len(content or "") < 2000:
                page.wait_for_load_state("domcontentloaded", timeout=10_000)
                content = page.content()
            html = content
            context.close()
            browser.close()
    except Exception as e:
        log(settings, f"Playwright error on Apple detail: {e}")
        return None

    return extract_description_from_html(html) if html else None
