# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import json
from typing import Optional
from bs4 import BeautifulSoup

from .config import Settings
from .http import get

def strip_html(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text_parts = []
    for el in soup.find_all(["p", "li", "h2", "h3", "h4", "br"]):
        if el.name == "li":
            text_parts.append("• " + el.get_text(" ", strip=True))
        elif el.name == "br":
            text_parts.append("\n")
        else:
            text_parts.append(el.get_text(" ", strip=True))
    text = "\n".join([t for t in text_parts if t.strip()])
    if not text.strip():
        text = soup.get_text("\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", text).strip()

def extract_job_description_from_jsonld(soup: BeautifulSoup) -> Optional[str]:
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "")
        except Exception:
            continue
        candidates = data if isinstance(data, list) else [data]
        for obj in candidates:
            if isinstance(obj, dict) and obj.get("@type") in ("JobPosting", ["JobPosting"]):
                desc_html = obj.get("description")
                if isinstance(desc_html, str) and desc_html.strip():
                    return strip_html(desc_html)
    return None

def heuristic_main_text(soup: BeautifulSoup) -> str:
    def score(el) -> int:
        text = el.get_text(" ", strip=True)
        return len(text) + 50 * len(el.find_all("li"))
    containers = []
    for sel in ["main", "article", "section", "div[id=content]", "div[role=main]"]:
        containers.extend(soup.select(sel))
    containers = [c for c in containers if c.get_text(strip=True)]
    if containers:
        best = max(containers, key=score)
        return strip_html(str(best))
    return strip_html(str(soup))

# NEW: used by both requests- and Playwright-based fetchers
def extract_description_from_html(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "lxml")

    # 1) JSON-LD JobPosting
    desc = extract_job_description_from_jsonld(soup)
    if desc and len(desc) > 80:
        return desc

    # 2) Meta description (short)
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        md = meta["content"].strip()
        if md and len(md) > 60:
            return md

    # 3) Heuristic main content
    return heuristic_main_text(soup)

# Existing path for non-Playwright companies
def extract_description(session, url: str, settings: Settings) -> Optional[str]:
    resp = get(session, url, settings)
    if not resp:
        return None
    return extract_description_from_html(resp.text)
