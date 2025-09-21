#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
from scraper.config import make_settings
from scraper.runner import run as run_pipeline

def parse_args():
    parser = argparse.ArgumentParser(description="Scrape FAANG job URLs + descriptions.")
    parser.add_argument(
        "--companies",
        nargs="*",
        default=["apple", "amazon", "google", "meta", "netflix"],
        choices=["apple", "amazon", "google", "meta", "netflix"],
        help="Subset of companies to scrape (default: all)."
    )
    parser.add_argument("--out-dir", default="data", help="Output directory (default: data).")
    parser.add_argument("--max-pages", type=int, default=None, help="Safety cap for paginated listings.")
    parser.add_argument("--sleep", type=float, default=None, help="Seconds to sleep between requests.")
    return parser.parse_args()

def main():
    args = parse_args()
    settings = make_settings(args.out_dir)
    if args.max_pages:
        settings.max_pages = args.max_pages
    if args.sleep is not None:
        settings.sleep_between_requests_sec = args.sleep
    summary = run_pipeline(args.companies, settings)
    print("Summary:", summary)

if __name__ == "__main__":
    main()
