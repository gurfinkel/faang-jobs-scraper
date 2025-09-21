import csv
import json
from datetime import datetime, timezone
from typing import Dict, List
from .config import Settings

def log(settings: Settings, msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S%z")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with settings.log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def load_seen(settings: Settings) -> set:
    if settings.seen_path.exists():
        try:
            return set(json.loads(settings.seen_path.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()

def save_seen(settings: Settings, seen: set):
    settings.seen_path.write_text(json.dumps(sorted(seen)), encoding="utf-8")

def write_outputs(settings: Settings, rows: List[Dict[str, str]]):
    if not rows:
        return
    # JSONL
    with settings.jsonl_path.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    # CSV
    csv_exists = settings.csv_path.exists()
    with settings.csv_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["company", "url", "description"])
        if not csv_exists:
            writer.writeheader()
        for r in rows:
            writer.writerow(r)
