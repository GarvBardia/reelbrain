"""One-time migration: adds the "Priority" Select property (High/Medium/Low,
plain text, no emoji) to your EXISTING live Saves database. Safe to re-run —
data_sources.update on an already-existing property just re-asserts the same
options, it doesn't duplicate or reset anything.

Why this is needed: unlike a new SELECT OPTION on an existing property (which
Notion auto-creates on first write), a brand-new PROPERTY generally needs to
be declared against the data source before the app writes to it — the
Priority code in app/notion_writer.py assumes the property already exists.
Run this once before deploying the Priority feature.

Usage:
    python scripts/add_priority_property.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app import notion_writer

PRIORITIES = ["High", "Medium", "Low"]


def main() -> None:
    client = notion_writer._client()
    ds_id = notion_writer._resolve_data_source_id(client, notion_writer.NOTION_DB_ID)

    client.data_sources.update(
        data_source_id=ds_id,
        properties={
            "Priority": {"select": {"options": [{"name": name} for name in PRIORITIES]}},
        },
    )
    print("Priority property added to the Saves database (options: High, Medium, Low).")
    print("Next: add the '🎯 Action Needed' view — see NOTION_VIEWS.md.")


if __name__ == "__main__":
    main()
