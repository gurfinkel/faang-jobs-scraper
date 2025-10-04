import re, time, json
from datetime import datetime, timezone
from typing import Tuple, Dict, Any
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

_IT_TITLE_RE = re.compile(
    r"\b(software|developer|engineer|sde|swe|frontend|back[- ]?end|full[- ]?stack|ios|android|"
    r"devops|sre|site reliability|platform|infra|cloud|security engineer|secops|"
    r"data engineer|data scientist|ml engineer|machine learning|ai|qa|test|automation|"
    r"systems engineer|network engineer|sysadmin|it support|help ?desk)\b",
    re.I,
)

def classify_category(title: str, desc: str) -> str:
    text = f"{title or ''} {desc or ''}"
    return "it" if _IT_TITLE_RE.search(text) else "other"

def parse_ldjson_job(html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    data: Dict[str, Any] = {}
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            blob = json.loads(script.string or "")
        except Exception:
            continue
        # Some pages wrap multiple objects in a list
        objs = blob if isinstance(blob, list) else [blob]
        for obj in objs:
            if isinstance(obj, dict) and obj.get("@type") in ("JobPosting", "jobposting"):
                data.update(obj)
                return data
    return {}

def parse_posted_at(html: str) -> int:
    jp = parse_ldjson_job(html)
    if "datePosted" in jp:
        try:
            dt = dateparser.parse(jp["datePosted"])
            return int(dt.replace(tzinfo=timezone.utc).timestamp())
        except Exception:
            pass
    # Fallback: now
    return int(time.time())

def extract_title(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    # Prefer H1
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    return ""

def parse_location_fields(html: str) -> Tuple[str, str, str, int]:
    """
    Return (country_iso2, admin1, city, remote_flag). Best-effort from ld+json.
    """
    soup = BeautifulSoup(html, "html.parser")
    jp = parse_ldjson_job(html)
    country = admin1 = city = ""
    remote = 0
    if jp:
        # Remote?
        if str(jp.get("jobLocationType", "")).lower() in ("telecommute", "remote"):
            remote = 1
        # jobLocation can be dict or list with 'address'
        locs = jp.get("jobLocation")
        if isinstance(locs, dict):
            locs = [locs]
        if isinstance(locs, list):
            for loc in locs:
                addr = loc.get("address") if isinstance(loc, dict) else None
                if isinstance(addr, dict):
                    country = (addr.get("addressCountry") or country or "").upper()
                    admin1  = addr.get("addressRegion") or admin1
                    city    = addr.get("addressLocality") or city
    # Fallback: look for a location label (e.g. Apple pages)
    if not country:
        loc_label = soup.find(id=lambda x: x and "joblocation" in x.lower())
        if loc_label:
            text = loc_label.get_text(strip=True)
            # Split the string like "Cupertino, California, United States"
            parts = [p.strip() for p in text.split(",") if p.strip()]
            if len(parts) >= 3:
                city, admin1, country = parts[-3], parts[-2], parts[-1]
            elif len(parts) == 2:
                admin1, country = parts[-2], parts[-1]
            # Convert to ISO‑style if needed (upper‑case country)
            country = country.upper()
    # Additional fallback: Amazon’s location div
    if not country:
        loc_div = soup.find('div', class_='location')
        if loc_div:
            # typical pattern: "Location: IT, Trentino-Alto Adige, Trento"
            text = loc_div.get_text(strip=True)
            text = text.replace('Location:', '').strip()
            parts = [p.strip() for p in text.split(',') if p.strip()]
            if len(parts) >= 3:
                country = parts[-1].upper()
                admin1 = parts[-2]
                city = parts[-3]
            elif len(parts) == 2:
                country = parts[-1].upper()
                admin1 = parts[-2]
    return country, admin1, city, remote

def extract_description_from_html(html: str) -> str:
    # Very simple: choose the longest <section>/<div> block text near 'description'
    soup = BeautifulSoup(html, "html.parser")
    for sel in ["[itemprop=description]", "section", "article", "div"]:
        blocks = soup.select(sel)
        best = max((b.get_text(" ", strip=True) or "" for b in blocks), key=len, default="")
        if best and len(best) > 200:
            return best
    # Fallback: whole page (trim)
    text = soup.get_text(" ", strip=True)
    return text[:4000]  # avoid overlong items
