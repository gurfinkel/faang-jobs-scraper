# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Dict, List
import time

from .config import Settings
from .http import make_session
from .io_utils import log, load_seen, save_seen, write_outputs
from .parsing import extract_description

# company discovery functions
from .companies import apple as apple_mod
from .companies import amazon as amazon_mod
from .companies import google as google_mod
from .companies import meta as meta_mod
from .companies import netflix as netflix_mod

def _company_map():
    return {
        "apple":   apple_mod.discover,
        "amazon":  amazon_mod.discover,
        "google":  google_mod.discover,
        "meta":    meta_mod.discover,      # Playwright for discovery
        "netflix": netflix_mod.discover,   # Playwright for discovery
    }

def process_company(name: str, discover_fn, session, settings: Settings) -> Dict[str, int]:
    log(settings, f"--- {name.upper()} --- discovering links")
    urls = discover_fn(session, settings)
    seen = load_seen(settings)
    new_rows = []
    fetched = 0
    added = 0

    for u in urls:
        if u in seen:
            continue

        # Use Playwright for Meta details; requests for others
        if name == "meta":
            desc = meta_mod.get_description(u, settings)
        elif name == "netflix":
            desc = netflix_mod.get_description(u, settings)
        else:
            desc = extract_description(session, u, settings)

        fetched += 1
        if desc:
            new_rows.append({"company": name, "url": u, "description": desc})
            seen.add(u)
            added += 1

        if fetched % 10 == 0:
            log(settings, f"{name}: fetched {fetched} detail pages...")

        time.sleep(settings.sleep_between_requests_sec)

    write_outputs(settings, new_rows)
    save_seen(settings, seen)
    log(settings, f"{name}: wrote {len(new_rows)} new rows")
    return {"pages_fetched": fetched, "new_rows": added}

def run(companies: List[str], settings: Settings) -> Dict[str, Dict[str, int]]:
    """Entry point used by main.py"""
    mapping = _company_map()
    session = make_session(settings)

    summary: Dict[str, Dict[str, int]] = {}
    for c in companies:
        if c not in mapping:
            log(settings, f"Unknown company '{c}' — skipping")
            continue
        try:
            summary[c] = process_company(c, mapping[c], session, settings)
        except Exception as e:
            log(settings, f"Error processing {c}: {e}")
    return summary

# Optional alias for convenience
run_pipeline = run

__all__ = ["run", "run_pipeline", "process_company"]
