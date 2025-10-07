import time
from typing import List
from ..config import Settings
from ..io_utils import log

BASE = "https://www.amazon.jobs"
API  = f"{BASE}/search.json"

def discover(session, settings: Settings) -> List[str]:
    urls = []
    offset = 0
    page_size = 100
    max_pages = settings.max_pages
    while offset < max_pages * page_size:
        params = {
            "result_limit": page_size,
            "offset": offset,
            "sort": "recent",
        }
        try:
            resp = session.get(API, params=params, timeout=settings.request_timeout)
            if resp.status_code != 200:
                log(settings, f"amazon: HTTP {resp.status_code} at offset={offset}")
                break
            data = resp.json()
        except Exception as e:
            log(settings, f"amazon: JSON parse error at offset {offset}: {e}")
            break

        jobs = data.get("jobs") or []
        if not jobs:
            break

        for j in jobs:
            path = j.get("job_path")
            if path:
                urls.append(BASE + path)

        offset += page_size
        time.sleep(settings.sleep_between_requests_sec)

    unique = sorted(set(urls))
    log(settings, f"amazon: discovered {len(unique)} URLs")
    return unique
