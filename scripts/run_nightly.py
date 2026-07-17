"""Runs the nightly cleanup job once. BUILD_SPEC.md §2.3.

Usage:
    python scripts/run_nightly.py

Meant to be invoked on a schedule (Render Cron Job, GitHub Actions on a cron
trigger, or a plain OS cron entry) — see README.md for setup.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make `python scripts/run_nightly.py` work as well as `python -m scripts.run_nightly`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app import nightly, store


def main() -> None:
    store.init_db()
    result = nightly.run()
    print(f"marked failed:        {result['marked_failed'] or '(none)'}")
    print(f"marked gate_expired:  {result['marked_gate_expired'] or '(none)'}")
    print(f"marked archived:      {result['marked_archived'] or '(none)'}")
    cookie_alert = result["cookie_alert"]
    print(f"cookie health:        {cookie_alert['cookie_health']}")
    if cookie_alert["alert_sent"]:
        print("cookie alert sent — see COOKIES.md to refresh the burner cookies")


if __name__ == "__main__":
    main()
