"""One-time migration: adds the "Named entities" multi-select property to the
EXISTING live Saves database (Phase G).

WHY THIS MATTERS: named_entities has been extracted since the research-context
work but was NEVER written to Notion. That made the single most specific
matching signal /attach has completely inert -- attach_matching's
NAMED_ENTITY_MATCH_WEIGHT scored against a field that was always empty on real
data. Persisting it is the prerequisite for any real accuracy gain.

Safe to re-run -- data_sources.update on an existing property just re-asserts
it, same convention as add_priority_property.py / add_plain_summary_property.py.

Usage:
    python scripts/add_named_entities_property.py
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
    # No fixed options: Notion auto-creates multi-select options on first write,
    # and named entities are open-ended by nature (every new tool is a new one).
    client.data_sources.update(
        data_source_id=ds_id,
        properties={"Named entities": {"multi_select": {}}},
    )
    print("'Named entities' multi-select property added to the Saves database.")


if __name__ == "__main__":
    main()
