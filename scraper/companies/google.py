import re
import time
from typing import List
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from ..config import Settings
from ..http import get
from ..io_utils import log

BASE = "https://careers.google.com"

def discover(session, settings: Settings) -> List[str]:
    urls = []
    empty_pages = 0
    for page in range(1, settings.max_pages + 1):
        u = f"{BASE}/jobs/results/?page={page}"
        resp = get(session, u, settings)
        if not resp:
            break
        soup = BeautifulSoup(resp.text, "lxml")
        found = 0
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/jobs/results/" in href and re.search(r"/jobs/results/\d", href):
                urls.append(urljoin(BASE, href))
                found += 1
        if found == 0:
            empty_pages += 1
            if empty_pages >= 3:
                break
        else:
            empty_pages = 0
        time.sleep(settings.sleep_between_requests_sec)
    unique = sorted(set(urls))
    log(settings, f"google: discovered {len(unique)} URLs")
    return unique
