"""Builds the weekly digest and writes it to Notion. LIVE — hits Gemini (optionally)
and Notion for real.

Usage:
    python scripts/weekly_digest.py

Schedule weekly (e.g. Sunday evening) the same way as the nightly job — see SCHEDULING.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make `python scripts/weekly_digest.py` work as well as `python -m scripts.weekly_digest`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app import digest, store


def main() -> None:
    store.init_db()
    result = digest.run()
    print(f"saves this week:     {result['save_count']}")
    print(f"AI summary included: {result['ai_summary_included']}")
    page = result["notion_page"]
    print(f"notion page:         {page['url'] if page else '(not written — see logs)'}")
    print("\n--- digest markdown ---\n")
    print(result["markdown"])


if __name__ == "__main__":
    main()
