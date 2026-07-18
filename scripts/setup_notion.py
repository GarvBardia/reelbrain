"""One-time script: creates the Saves + Creators Notion databases per DATA_SCHEMA.md §1.

Usage:
    NOTION_TOKEN=... NOTION_PARENT_PAGE_ID=... python scripts/setup_notion.py

Prints the two database IDs to paste into .env as NOTION_DB_ID / NOTION_CREATORS_DB_ID.
Relation property auto-naming varies slightly by Notion API version — after running,
open both databases once in the Notion UI and confirm "Creator"/"Related"/"Save count"
look right; rename anything odd (this only affects labels, not the join keys the
pipeline actually writes to).

NOTE: Notion's 2025-09-03+ API requires:
  - new databases created with an "initial_data_source" wrapper around "properties"
  - relation properties to reference a "data_source_id", not a "database_id"
This script uses that shape throughout.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Make `python scripts/setup_notion.py` work as well as `python -m scripts.setup_notion`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from notion_client import Client

load_dotenv()

NOTION_TOKEN = os.environ["NOTION_TOKEN"].strip()
PARENT_PAGE_ID = os.environ["NOTION_PARENT_PAGE_ID"].strip()

CONTENT_TYPES = ["tutorial", "insight", "resource_drop", "motivation", "news", "entertainment", "unknown"]
STATUSES = ["📥 Inbox", "⏳ Awaiting DM", "✅ Processed/Reviewed", "⚠️ Failed — retry", "🗑 Low signal", "🕳 Gate expired"]
VALUE_SCORES = ["1", "2", "3", "4", "5"]
# Plain text, no emoji — see app/gemini_pipe.py compute_priority().
PRIORITIES = ["High", "Medium", "Low"]


def title(text: str) -> list[dict]:
    return [{"type": "text", "text": {"content": text}}]


def select_options(names: list[str]) -> dict:
    return {"select": {"options": [{"name": n} for n in names]}}


def main() -> None:
    client = Client(auth=NOTION_TOKEN)

    creators_db = client.databases.create(
        parent={"type": "page_id", "page_id": PARENT_PAGE_ID},
        title=title("🎤 Creators"),
        initial_data_source={
            "properties": {
                "Username": {"title": {}},
                "Full name": {"rich_text": {}},
                "Core source": {"checkbox": {}},
                "Profile URL": {"url": {}},
            }
        },
    )
    creators_db_id = creators_db["id"]
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
                "Priority": select_options(PRIORITIES),
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

    # Self-relation "Related" — needs the Saves data source's own ID, only known post-creation.
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

    # Find the reverse-relation property Notion auto-added to Creators DB so we can
    # build rollups over it (Save count, Primary topics).
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
    print(f"NOTION_CREATORS_DB_ID={creators_db_id}")
    print("\nPaste these into .env, then open both databases in Notion once to sanity-check labels.")


if __name__ == "__main__":
    try:
        main()
    except KeyError as exc:
        sys.exit(f"missing required env var: {exc}")