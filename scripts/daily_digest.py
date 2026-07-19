"""Builds the daily reflection digest and writes it to Notion (+ ntfy if
configured). LIVE — hits Notion (and ntfy.sh, if NTFY_TOPIC is set) for real.

Usage:
    python scripts/daily_digest.py

Schedule daily (evening) via an external scheduler hitting POST /daily-digest —
see SCHEDULING.md. This script is the same code path, for local/manual runs.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make `python scripts/daily_digest.py` work as well as `python -m scripts.daily_digest`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app import digest, store


def main() -> None:
    store.init_db()
    result = digest.run_daily()
    print(f"saves today:          {result['save_count']}")
    print(f"high priority:        {result['high_priority_count']}")
    page = result["notion_page"]
    print(f"notion page:          {page['url'] if page else '(not written — see logs)'}")
    print(f"ntfy push sent:       {result['ntfy_sent']}")
    print("\n--- digest markdown ---\n")
    print(result["markdown"])


if __name__ == "__main__":
    main()
