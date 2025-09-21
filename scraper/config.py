from dataclasses import dataclass
from pathlib import Path

@dataclass
class Settings:
    out_dir: Path
    jsonl_path: Path
    csv_path: Path
    seen_path: Path
    log_path: Path
    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
    request_timeout: int = 20
    sleep_between_requests_sec: float = 1.2
    max_pages: int = 200

def make_settings(out_dir: str | Path = "data") -> Settings:
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    return Settings(
        out_dir=out,
        jsonl_path=out / "faang_jobs.jsonl",
        csv_path=out / "faang_jobs.csv",
        seen_path=out / "seen_urls.json",
        log_path=out / "faang_scraper.log",
    )
