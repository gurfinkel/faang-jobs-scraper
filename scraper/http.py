import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from typing import Optional
from .config import Settings
from .io_utils import log

def make_session(settings: Settings) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": settings.user_agent, "Accept-Language": "en"})
    retries = Retry(
        total=5, backoff_factor=0.8,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=10)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s

def get(session: requests.Session, url: str, settings: Settings) -> Optional[requests.Response]:
    try:
        resp = session.get(url, timeout=settings.request_timeout)
        if resp.status_code == 200:
            return resp
        log(settings, f"GET {url} -> HTTP {resp.status_code}")
    except requests.RequestException as e:
        log(settings, f"GET {url} failed: {e}")
    return None
