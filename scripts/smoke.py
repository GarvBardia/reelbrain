"""Live end-to-end smoke test — hits real yt-dlp/Gemini/Notion, no mocks.

Usage:
    python scripts/smoke.py https://www.instagram.com/reel/XXXXXXXX/
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make `python scripts/smoke.py` work as well as `python -m scripts.smoke`:
# ensure the repo root (parent of scripts/) is on sys.path so `app` imports.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app import fetcher, store
from app.main import run_pipeline


def main(url: str) -> None:
    store.init_db()
    shortcode = fetcher.normalize_url(url)
    permalink = f"https://www.instagram.com/reel/{shortcode}/"

    existing = store.get_by_shortcode(shortcode)
    if not existing:
        store.insert_processing(shortcode, permalink, note="smoke-test")

    print(f"Running pipeline for shortcode={shortcode} ...")
    run_pipeline(shortcode, permalink, note="smoke-test")

    row = store.get_by_shortcode(shortcode)
    print("\n--- result ---")
    print(f"status:          {row['status']}")
    print(f"notion_page_url: {row['notion_page_url']}")
    print(f"gate_keyword:    {row['gate_keyword']}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python scripts/smoke.py <reel_url>")
    main(sys.argv[1])
