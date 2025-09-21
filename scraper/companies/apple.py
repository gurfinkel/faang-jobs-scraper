import time
from typing import List
from bs4 import BeautifulSoup
from ..config import Settings
from ..http import get
from ..io_utils import log

APPLE_SITEMAPS = [
    "https://jobs.apple.com/sitemap/sitemap-jobs-en-us.xml",
    # add more locales if needed:
    # "https://jobs.apple.com/sitemap/sitemap-jobs-en-gb.xml",
    # "https://jobs.apple.com/sitemap/sitemap-jobs-en-ca.xml",
]

def discover(session, settings: Settings) -> List[str]:
    urls = []
    for sm in APPLE_SITEMAPS:
        resp = get(session, sm, settings)
        if not resp:
            continue
        soup = BeautifulSoup(resp.text, "lxml-xml")
        for loc in soup.find_all("loc"):
            u = loc.get_text(strip=True)
            if "/details/" in u:
                urls.append(u)
        time.sleep(settings.sleep_between_requests_sec)
    unique = sorted(set(urls))
    log(settings, f"apple: discovered {len(unique)} URLs")
    return unique
