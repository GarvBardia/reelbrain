"""One-time migration: adds the "Suggested action" rich_text property to the
EXISTING live Saves database. Safe to re-run — data_sources.update on an
already-existing property just re-asserts it, same convention as
scripts/add_priority_property.py.

Usage:
    python scripts/add_suggested_action_property.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app import notion_writer


def main() -> None:
    client = notion_writer._client()
    ds_id = notion_writer._resolve_data_source_id(client, notion_writer.NOTION_DB_ID)

    client.data_sources.update(
        data_source_id=ds_id,
        properties={"Suggested action": {"rich_text": {}}},
    )
    print("'Suggested action' rich_text property added to the Saves database.")


if __name__ == "__main__":
    main()
