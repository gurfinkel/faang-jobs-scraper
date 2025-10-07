from __future__ import annotations
import os, time
from typing import Dict, List
from .config import Settings
from .io_utils import log
from .http import make_session
from .parsing import (
    extract_title, extract_description_from_html,
    parse_posted_at, parse_location_fields, classify_category,
)
from .companies import apple as apple_mod
from .companies import amazon as amazon_mod
from .companies import google as google_mod
from .companies import meta as meta_mod
from .companies import netflix as netflix_mod

USE_DDB = bool(os.environ.get("TABLE_NAME"))
if USE_DDB:
    from storage import dynamo as ddb

def _company_map():
    return {
        "apple":   apple_mod.discover,
        "amazon":  amazon_mod.discover,
        "google":  google_mod.discover,
        "meta":    meta_mod.discover,
        "netflix": netflix_mod.discover,
    }

def _fetch_html(session, url: str, settings: Settings) -> str:
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text

def process_company(name: str, discover_fn, session, settings: Settings) -> Dict[str, int]:
    log(settings, f"--- {name.upper()} --- discovering links")
    urls = discover_fn(session, settings)
    discovered = list(dict.fromkeys(urls))
    log(settings, f"{name}: discovered {len(discovered)} URLs")

    # Compute 'todo' = new URLs only (in AWS mode)
    if USE_DDB:
        existing = ddb.list_urls(name)
        todo = [u for u in discovered if u not in existing]
        if settings.max_new_per_run is not None:
            todo = todo[: settings.max_new_per_run]
        log(settings, f"{name}: existing {len(existing)}; will fetch {len(todo)} new")
    else:
        todo = discovered[:]  # local mode simplification

    fetched = 0
    written = 0
    chunk: Dict[str, Dict] = {}

    def flush():
        nonlocal written, chunk
        if chunk:
            if USE_DDB:
                ddb.batch_upsert_items(name, chunk)
            written += len(chunk)
            log(settings, f"{name}: chunk upsert -> {len(chunk)} (total new {written})")
            chunk = {}

    # JS-backed sites: use their batch page-readers if you already have them.
    if name in ("meta", "netflix") and todo:
        # If your modules return HTML per URL, adapt here.
        # For now we keep the existing description batch and fill minimal fields.
        batch_desc = (meta_mod if name == "meta" else netflix_mod).get_descriptions_batch(todo, settings)
        for i, u in enumerate(todo, 1):
            desc = batch_desc.get(u) or ""
            title = ""  # unknown (Playwright path didn't return HTML)
            posted_at = int(time.time())
            loc_country, loc_admin1, loc_city, remote = "", "", "", 0
            cat = classify_category(title, desc)
            if cat != "it":
                continue  # store only IT if you want to keep table lean
            chunk[u] = {
                "title": title, "description": desc, "category": cat,
                "posted_at": posted_at, "loc_country": loc_country,
                "loc_admin1": loc_admin1, "loc_city": loc_city, "remote": remote,
            }
            if i % 10 == 0:
                log(settings, f"{name}: fetched {i} detail pages...")
            if len(chunk) >= settings.chunk_upsert_size:
                flush()
        fetched = len(todo)
        flush()
    else:
        # Non-JS: fetch HTML & parse fields
        for i, u in enumerate(todo, 1):
            try:
                html = _fetch_html(session, u, settings)
                title = extract_title(html)
                desc  = extract_description_from_html(html)
                posted_at = parse_posted_at(html)
                ctry, admin1, city, remote = parse_location_fields(html)
                if not ctry:
                    log(settings, f"{name}: missing country for {u}")
                    continue
                cat = classify_category(title, desc)
                if cat != "it":
                    continue
                chunk[u] = {
                    "title": title, "description": desc, "category": cat,
                    "posted_at": posted_at, "loc_country": ctry,
                    "loc_admin1": admin1, "loc_city": city, "remote": remote,
                }
            except Exception as e:
                log(settings, f"{name}: error fetching {u}: {e}")
            if i % 10 == 0:
                log(settings, f"{name}: fetched {i} detail pages...")
            if len(chunk) >= settings.chunk_upsert_size:
                flush()
            fetched = i
        flush()

    # Finalize deletions after we've upserted chunks
    if USE_DDB:
        stats = ddb.finalize_company(name, discovered)
        log(settings, f"{name}: finalize -> {stats}")

    log(settings, f"{name}: wrote {written} new rows")
    return {"pages_fetched": fetched, "new_rows": written}

def run(companies: List[str], settings: Settings) -> Dict[str, Dict[str, int]]:
    summary: Dict[str, Dict[str, int]] = {}
    # Acquire lock (AWS mode)
    lock_acquired = False
    if USE_DDB:
        from .config import Settings as _S
        lock_acquired = ddb.acquire_lock(settings.lock_key, settings.lock_ttl_sec)
        if not lock_acquired:
            log(settings, "Another run is in progress; exiting.")
            return summary
    try:
        with make_session(settings) as session:
            cmap = _company_map()
            targets = [c for c in (companies or list(cmap.keys())) if c in cmap]
            for name in targets:
                try:
                    summary[name] = process_company(name, cmap[name], session, settings)
                except Exception as e:
                    log(settings, f"Error processing {name}: {e}")
        log(settings, f"Summary: {summary}")
        return summary
    finally:
        if USE_DDB and lock_acquired:
            ddb.release_lock(settings.lock_key)
