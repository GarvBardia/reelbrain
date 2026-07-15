"""One-time RECOVERY script: recreates only the Saves database, linking it to
the EXISTING Creators database (does not touch or duplicate Creators).

Usage:
    Fill in .env as usual, plus make sure NOTION_CREATORS_DB_ID is already set
    to your existing, correctly-built Creators database ID
    (cdf9f4f1-1fc1-4405-9782-4663ec35c068 in this case).

    python scripts/setup_notion_saves_only.py

Prints the new NOTION_DB_ID to paste into .env.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Make `python scripts/setup_notion_saves_only.py` work as well as
# `python -m scripts.setup_notion_saves_only`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from notion_client import Client

load_dotenv()

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
PARENT_PAGE_ID = os.environ["NOTION_PARENT_PAGE_ID"]
EXISTING_CREATORS_DB_ID = os.environ["NOTION_CREATORS_DB_ID"]

CONTENT_TYPES = ["tutorial", "insight", "resource_drop", "motivation", "news", "entertainment", "unknown"]
STATUSES = ["📥 Inbox", "⏳ Awaiting DM", "✅ Processed/Reviewed", "⚠️ Failed — retry", "🗑 Low signal", "🕳 Gate expired"]
VALUE_SCORES = ["1", "2", "3", "4", "5"]


def title(text: str) -> list[dict]:
    return [{"type": "text", "text": {"content": text}}]


def select_options(names: list[str]) -> dict:
    return {"select": {"options": [{"name": n} for n in names]}}


def main() -> None:
    client = Client(auth=NOTION_TOKEN)

    # Look up the existing Creators database's data source ID (needed for the relation).
    creators_db = client.databases.retrieve(database_id=EXISTING_CREATORS_DB_ID)
    creators_data_source_id = creators_db["data_sources"][0]["id"]

    saves_db = client.databases.create(
        parent={"type": "page_id", "page_id": PARENT_PAGE_ID},
        title=title("📼 Saves"),
        initial_data_source={
            "properties": {
                "Title": {"title": {}},
                "Status": select_options(STATUSES),
                "Content type": select_options(CONTENT_TYPES),
                "Topics": {"multi_select": {"options": []}},
                "Creator": {
                    "relation": {
                        "data_source_id": creators_data_source_id,
                        "type": "dual_property",
                        "dual_property": {},
                    }
                },
                "Reel URL": {"url": {}},
                "Posted at": {"date": {}},
                "Value score": select_options(VALUE_SCORES),
                "Comment gate": {"checkbox": {}},
                "Gate keyword": {"rich_text": {}},
                "Gate resource": {"url": {}},
                "My note": {"rich_text": {}},
                "Shortcode": {"rich_text": {}},
            }
        },
    )
    saves_db_id = saves_db["id"]
    saves_data_source_id = saves_db["data_sources"][0]["id"]

    # Self-relation "Related"
    client.data_sources.update(
        data_source_id=saves_data_source_id,
        properties={
            "Related": {
                "relation": {
                    "data_source_id": saves_data_source_id,
                    "type": "dual_property",
                    "dual_property": {},
                }
            }
        },
    )

    # Find the reverse-relation property Notion auto-added to Creators DB, then
    # (re)build the rollups over it — safe to run even if they already exist.
    creators_schema = client.data_sources.retrieve(data_source_id=creators_data_source_id)["properties"]
    reverse_relation_name = next(
        name for name, prop in creators_schema.items()
        if prop["type"] == "relation" and prop["relation"]["data_source_id"] == saves_data_source_id
    )

    client.data_sources.update(
        data_source_id=creators_data_source_id,
        properties={
            "Save count": {
                "rollup": {
                    "relation_property_name": reverse_relation_name,
                    "rollup_property_name": "Title",
                    "function": "count",
                }
            },
            "Primary topics": {
                "rollup": {
                    "relation_property_name": reverse_relation_name,
                    "rollup_property_name": "Topics",
                    "function": "show_original",
                }
            },
        },
    )

    print(f"NOTION_DB_ID={saves_db_id}")
    print("\nPaste this into .env as NOTION_DB_ID (replacing the old value). Creators DB left untouched.")


if __name__ == "__main__":
    try:
        main()
    except KeyError as exc:
        sys.exit(f"missing required env var: {exc}")
