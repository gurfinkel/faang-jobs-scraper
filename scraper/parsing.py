"""
HTML parsing helpers for job pages.

Highlights / changes:
- Robust JSON-LD parsing that searches inside @graph and nested structures.
- Eightfold (PCS) JSON extraction (all_applicable_locations, positions[].location,
  work_location_option) to recover country/admin1/city/remote when JSON-LD is absent.
- Conservative text heuristic for country-only pipeline postings (e.g., Apple retail).
- Safer posted_at parsing.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Tuple, Union

from bs4 import BeautifulSoup
from dateutil import parser as dateparser

# --- Simple IT classifier ----------------------------------------------------

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


# --- JSON-LD JobPosting extraction ------------------------------------------

def _first_jobposting(node: Union[Dict[str, Any], Iterable, str, int, float, None]) -> Dict[str, Any]:
    """Depth-first search for a dict whose @type contains 'JobPosting' (case-insensitive)."""
    def is_jobposting(d: Dict[str, Any]) -> bool:
        t = d.get("@type")
        if not t:
            return False
        if isinstance(t, str):
            return t.lower() == "jobposting"
        if isinstance(t, (list, tuple)):
            return any(isinstance(x, str) and x.lower() == "jobposting" for x in t)
        return False

    if isinstance(node, dict):
        if is_jobposting(node):
            return node
        # Common containers that wrap JobPosting
        for key in ("@graph", "graph", "mainEntity", "itemListElement"):
            if key in node:
                found = _first_jobposting(node[key])
                if found:
                    return found
        for v in node.values():
            found = _first_jobposting(v)
            if found:
                return found
    elif isinstance(node, (list, tuple)):
        for it in node:
            found = _first_jobposting(it)
            if found:
                return found
    return {}

def parse_ldjson_job(html: str) -> Dict[str, Any]:
    """
    Return the first JSON-LD JobPosting found anywhere (incl. nested @graph).
    """
    soup = BeautifulSoup(html, "html.parser")
    # Match any 'application/ld+json' variant
    for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        raw = script.string or script.get_text() or ""
        if not raw.strip():
            continue
        try:
            blob = json.loads(raw)
        except Exception:
            continue
        job = _first_jobposting(blob)
        if job:
            return job
    return {}


# --- Posted date, title ------------------------------------------------------

def parse_posted_at(html: str) -> int:
    """
    Try JSON-LD 'datePosted', else now (UTC).
    """
    jp = parse_ldjson_job(html)
    if "datePosted" in jp:
        try:
            dt = dateparser.parse(str(jp["datePosted"]).strip())
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return int(dt.timestamp())
        except Exception:
            pass
    return int(time.time())

def extract_title(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    return ""


# --- Location parsing --------------------------------------------------------

_COUNTRY_ALIASES = {
    "united states of america": "US",
    "united states": "US",
    "u.s.": "US", "u.s.a.": "US", "usa": "US",
    "united kingdom": "GB", "uk": "GB", "england": "GB", "scotland": "GB", "wales": "GB",
    "ireland": "IE",
    "canada": "CA",
    "australia": "AU",
    "germany": "DE",
    "france": "FR",
    "spain": "ES",
    "italy": "IT",
    "netherlands": "NL",
    "sweden": "SE",
    "norway": "NO",
    "denmark": "DK",
    "switzerland": "CH",
    "japan": "JP",
    "korea, republic of": "KR", "south korea": "KR",
    "india": "IN",
    "brazil": "BR",
    "mexico": "MX",
    "singapore": "SG",
    "united arab emirates": "AE", "uae": "AE",
}

def _norm_country(name: str) -> str:
    if not name:
        return ""
    key = name.strip().lower()
    key = re.sub(r"\s+\(.*\)$", "", key)  # strip trailing (…)
    if len(name.strip()) == 2 and name.strip().isalpha():
        return name.strip().upper()
    return _COUNTRY_ALIASES.get(key, "")

def _try_eightfold_locations(html: str) -> Tuple[str, str, str, int]:
    """
    Parse Eightfold-style JSON embedded in the HTML.
    Looks for:
      - "all_applicable_locations":[{city, state, country}]
      - "positions":[{location: "City,State,Country", work_location_option: "..."}]
    """
    country = admin1 = city = ""
    remote = 0

    # 1) Explicit array with structured fields
    m = re.search(r'"all_applicable_locations"\s*:\s*(\[[^\]]*\])', html, re.S)
    if m:
        try:
            arr = json.loads(m.group(1))
            if isinstance(arr, list) and arr:
                loc = arr[0] if isinstance(arr[0], dict) else {}
                city = (loc.get("city") or loc.get("addressLocality") or "").strip()
                admin1 = (loc.get("state") or loc.get("addressRegion") or "").split(",")[0].strip()
                country = _norm_country(loc.get("country") or loc.get("addressCountry") or "")
                return country, admin1, city, remote
        except Exception:
            pass

    # 2) Fallback: positions[].location "City,State,Country"
    m = re.search(r'"positions"\s*:\s*\[(\{.*?\})\]', html, re.S)
    if m:
        try:
            first = json.loads(m.group(1))
            loc_str = first.get("location") or ""
            if loc_str:
                parts = [p.strip() for p in loc_str.split(",") if p.strip()]
                if len(parts) >= 3:
                    city = parts[0]
                    admin1 = parts[1]
                    country = _norm_country(parts[-1])
            wlo = (first.get("work_location_option") or "").lower()
            if wlo == "remote":
                remote = 1
            return country, admin1, city, remote
        except Exception:
            pass

    # 3) Remote hint only
    if re.search(r'"work_location_option"\s*:\s*"remote"', html, re.I):
        remote = 1

    return "", "", "", remote

def parse_location_fields(html: str) -> Tuple[str, str, str, int]:
    """
    Return (country_iso2, admin1, city, remote_flag).
    Strategy:
      1) JSON-LD JobPosting (incl. nested @graph)
      2) Eightfold/PCS embedded JSON
      3) Light heuristics (e.g., Apple retail country-only pages)
      4) A few site-specific crumbs kept from legacy logic
    """
    soup = BeautifulSoup(html, "html.parser")
    country = admin1 = city = ""
    remote = 0

    # 1) JSON-LD
    jp = parse_ldjson_job(html)
    if jp:
        jlt = str(jp.get("jobLocationType", "")).lower()
        if jlt in ("telecommute", "remote"):
            remote = 1

        # applicantLocationRequirements.eligibleRegion can contain address
        elig = jp.get("applicantLocationRequirements")
        elig_list = elig if isinstance(elig, list) else ([elig] if isinstance(elig, dict) else [])
        for e in elig_list:
            region = e.get("eligibleRegion") if isinstance(e, dict) else None
            if isinstance(region, dict):
                country = _norm_country(region.get("addressCountry") or country or "")
                admin1 = region.get("addressRegion") or admin1
                city = region.get("addressLocality") or city

        # jobLocation.{address{...}}
        locs = jp.get("jobLocation")
        locs = locs if isinstance(locs, list) else ([locs] if isinstance(locs, dict) else [])
        for loc in locs:
            addr = loc.get("address") if isinstance(loc, dict) else None
            if isinstance(addr, dict):
                country = _norm_country(addr.get("addressCountry") or country or "")
                admin1 = addr.get("addressRegion") or admin1
                city = addr.get("addressLocality") or city

    # 2) Eightfold JSON
    if not country:
        ef_country, ef_admin1, ef_city, ef_remote = _try_eightfold_locations(html)
        if ef_country:
            country, admin1, city = ef_country, ef_admin1, ef_city
        remote = max(remote, ef_remote)

    # 3) Heuristic for country-only pages (e.g., Apple "United States")
    if not country:
        head_text = " ".join((
            (soup.h1.get_text(" ", strip=True) if soup.h1 else ""),
            soup.get_text(" ", strip=True)[:500],
        ))
        if re.search(r"\bUnited States\b", head_text, re.I):
            country = "US"
        elif re.search(r"\bUnited Kingdom\b", head_text, re.I):
            country = "GB"

    # 4) Legacy crumbs (labels / analytics / Amazon-style lists)
    if not country:
        loc_label = soup.find(id=lambda x: x and "location" in x.lower())
        if loc_label:
            text = loc_label.get_text(strip=True)
            parts = [p.strip() for p in text.split(",") if p.strip()]
            if len(parts) >= 3:
                city, admin1, country = parts[-3], parts[-2], _norm_country(parts[-1]) or country
            elif len(parts) == 2:
                admin1, country = parts[-2], _norm_country(parts[-1]) or country

    if not country:
        loc_li = soup.select_one("div.location-icon ul.association-content li")
        if loc_li:
            text = loc_li.get_text(strip=True)
            parts = [p.strip() for p in text.split(",") if p.strip()]
            if len(parts) >= 3:
                country, admin1, city = _norm_country(parts[0]) or country, parts[1], parts[2]
            elif len(parts) == 2:
                country, city = _norm_country(parts[0]) or country, parts[1]

    if not country:
        match = re.search(r'"dimension8":"([^"]+)"', html)
        if match:
            parts = [p.strip() for p in match.group(1).split(",") if p.strip()]
            if len(parts) >= 3:
                country, admin1, city = _norm_country(parts[0]) or country, parts[1], parts[2]
            elif len(parts) == 2:
                country, city = _norm_country(parts[0]) or country, parts[1]

    return country, admin1, city, remote


# --- Description extraction --------------------------------------------------

def extract_description_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    # Prefer structured containers
    for sel in [
        "[itemprop=description]",
        "[data-testid='job-details']",
        "[data-testid='job-description']",
        "article", "section", "main", "div",
    ]:
        blocks = soup.select(sel)
        best = max((b.get_text(" ", strip=True) or "" for b in blocks), key=len, default="")
        if best and len(best) > 200:
            return best
    # Fallback: whole page (trim)
    text = soup.get_text(" ", strip=True)
    return text[:4000]
