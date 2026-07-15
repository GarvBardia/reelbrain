"""Notion page creation/update. DATA_SCHEMA.md §1 (properties + page body layout).

NOTE: Notion's 2025-09-03+ API requires querying and creating pages against a
"data source" (the actual table), not the "database" (the outer container),
via data_source_id instead of database_id. This file resolves each
database's data source ID once (cached) and uses it everywhere.
"""
from __future__ import annotations

import os
from typing import Optional

from app.models import Extraction, ReelData

# .strip(): env values pasted into a hosting dashboard often carry a trailing
# newline, which makes an illegal HTTP header value (httpx.LocalProtocolError)
# when the token goes into an Authorization header. Strip every credential/ID.
NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "").strip()
NOTION_DB_ID = os.environ.get("NOTION_DB_ID", "").strip()
NOTION_CREATORS_DB_ID = os.environ.get("NOTION_CREATORS_DB_ID", "").strip()

STATUS_LABELS = {
    "done": "📥 Inbox",
    "awaiting_dm": "⏳ Awaiting DM",
    "failed": "⚠️ Failed — retry",
    "low_signal": "🗑 Low signal",
    "gate_expired": "🕳 Gate expired",
}

_data_source_id_cache: dict[str, str] = {}


def _client():
    from notion_client import Client

    return Client(auth=NOTION_TOKEN)


def _resolve_data_source_id(client, database_id: str) -> str:
    """Look up (and cache) the data source ID for a given database ID."""
    if database_id in _data_source_id_cache:
        return _data_source_id_cache[database_id]
    db = client.databases.retrieve(database_id=database_id)
    data_source_id = db["data_sources"][0]["id"]
    _data_source_id_cache[database_id] = data_source_id
    return data_source_id


def _get_or_create_creator(client, username: Optional[str], fullname: Optional[str]) -> Optional[str]:
    if not username or not NOTION_CREATORS_DB_ID:
        return None
    creators_ds_id = _resolve_data_source_id(client, NOTION_CREATORS_DB_ID)
    results = client.data_sources.query(
        data_source_id=creators_ds_id,
        filter={"property": "Username", "title": {"equals": username}},
    )["results"]
    if results:
        return results[0]["id"]
    page = client.pages.create(
        parent={"type": "data_source_id", "data_source_id": creators_ds_id},
        properties={
            "Username": {"title": [{"text": {"content": username}}]},
            "Full name": {"rich_text": [{"text": {"content": fullname or ""}}]},
        },
    )
    return page["id"]


def _rich_text(content: str) -> list[dict]:
    return [{"type": "text", "text": {"content": content[:2000]}}] if content else []


def _build_properties(
    reel: ReelData,
    extraction: Optional[Extraction],
    status: str,
    note: Optional[str],
    creator_page_id: Optional[str],
    gate_resource_url: Optional[str],
    related_page_ids: Optional[list[str]] = None,
) -> dict:
    title = (extraction.main_point if extraction else reel.caption or reel.permalink)[:100]
    props: dict = {
        "Title": {"title": [{"text": {"content": title}}]},
        "Status": {"select": {"name": STATUS_LABELS[status]}},
        "Reel URL": {"url": reel.permalink},
        "Shortcode": {"rich_text": _rich_text(reel.shortcode)},
        "My note": {"rich_text": _rich_text(note or "")},
    }
    if reel.taken_at:
        props["Posted at"] = {"date": {"start": reel.taken_at}}
    if creator_page_id:
        props["Creator"] = {"relation": [{"id": creator_page_id}]}
    if gate_resource_url:
        props["Gate resource"] = {"url": gate_resource_url}
    if related_page_ids:
        # "Related" is a dual_property self-relation (see scripts/setup_notion.py) —
        # Notion automatically adds the reverse link on each related page too, so we
        # only ever need to set this side of it.
        props["Related"] = {"relation": [{"id": pid} for pid in related_page_ids]}
    if extraction:
        props["Content type"] = {"select": {"name": extraction.content_type}}
        props["Topics"] = {"multi_select": [{"name": t} for t in extraction.topic_tags]}
        props["Value score"] = {"select": {"name": str(extraction.value_score)}}
        props["Comment gate"] = {"checkbox": extraction.comment_gate.detected}
        if extraction.comment_gate.keyword:
            props["Gate keyword"] = {"rich_text": _rich_text(extraction.comment_gate.keyword)}
    return props


def _build_children(extraction: Optional[Extraction], caption: Optional[str]) -> list[dict]:
    blocks: list[dict] = []
    if extraction:
        blocks.append({
            "object": "block", "type": "callout",
            "callout": {"rich_text": _rich_text(extraction.main_point), "icon": {"emoji": "💡"}},
        })
        for point in extraction.supporting_points:
            blocks.append({
                "object": "block", "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": _rich_text(point)},
            })
        for step in extraction.steps_or_framework:
            blocks.append({
                "object": "block", "type": "numbered_list_item",
                "numbered_list_item": {"rich_text": _rich_text(step)},
            })
        for resource in extraction.resources_mentioned:
            if resource.url_if_stated:
                blocks.append({
                    "object": "block", "type": "bookmark",
                    "bookmark": {"url": resource.url_if_stated},
                })
            else:
                blocks.append({
                    "object": "block", "type": "paragraph",
                    "paragraph": {"rich_text": _rich_text(f"{resource.name} ({resource.type})")},
                })
        for quote in extraction.quotable_lines:
            blocks.append({
                "object": "block", "type": "quote",
                "quote": {"rich_text": _rich_text(quote)},
            })
        transcript_text = extraction.transcript or (
            "(no speech detected)" if extraction.has_speech is False else "(unavailable)"
        )
        blocks.append({
            "object": "block", "type": "toggle",
            "toggle": {
                "rich_text": _rich_text("Transcript"),
                "children": [{
                    "object": "block", "type": "paragraph",
                    "paragraph": {"rich_text": _rich_text(transcript_text)},
                }],
            },
        })
    blocks.append({
        "object": "block", "type": "toggle",
        "toggle": {
            "rich_text": _rich_text("Raw caption"),
            "children": [{
                "object": "block", "type": "paragraph",
                "paragraph": {"rich_text": _rich_text(caption or "(no caption)")},
            }],
        },
    })
    return blocks


def create_page(
    reel: ReelData,
    extraction: Optional[Extraction],
    status: str,
    note: Optional[str] = None,
    gate_resource_url: Optional[str] = None,
    related_page_ids: Optional[list[str]] = None,
) -> dict:
    client = _client()
    creator_page_id = _get_or_create_creator(client, reel.creator_username, reel.creator_fullname)
    properties = _build_properties(
        reel, extraction, status, note, creator_page_id, gate_resource_url, related_page_ids
    )
    children = _build_children(extraction, reel.caption)
    saves_ds_id = _resolve_data_source_id(client, NOTION_DB_ID)
    page = client.pages.create(
        parent={"type": "data_source_id", "data_source_id": saves_ds_id},
        properties=properties,
        children=children,
    )
    return {"page_id": page["id"], "url": page["url"], "creator_page_id": creator_page_id}


def update_page(
    page_id: str,
    reel: ReelData,
    extraction: Optional[Extraction],
    status: str,
    note: Optional[str] = None,
    gate_resource_url: Optional[str] = None,
    related_page_ids: Optional[list[str]] = None,
) -> dict:
    """Used by /retry: replaces properties + body blocks on an existing page."""
    client = _client()
    creator_page_id = _get_or_create_creator(client, reel.creator_username, reel.creator_fullname)
    properties = _build_properties(
        reel, extraction, status, note, creator_page_id, gate_resource_url, related_page_ids
    )
    page = client.pages.update(page_id=page_id, properties=properties)

    existing = client.blocks.children.list(block_id=page_id)["results"]
    for block in existing:
        client.blocks.delete(block_id=block["id"])
    children = _build_children(extraction, reel.caption)
    client.blocks.children.append(block_id=page_id, children=children)
    return {"page_id": page["id"], "url": page["url"], "creator_page_id": creator_page_id}


def set_status(page_id: str, status: str) -> None:
    """BUILD_SPEC 2.3 (nightly job): flip Status only, no body-block rebuild."""
    client = _client()
    client.pages.update(page_id=page_id, properties={"Status": {"select": {"name": STATUS_LABELS[status]}}})


def set_status_and_gate_resource(page_id: str, status: str, gate_resource_url: str) -> None:
    """BUILD_SPEC 2.2 (/attach): flip Awaiting DM -> Inbox and record the DM'd link."""
    client = _client()
    client.pages.update(
        page_id=page_id,
        properties={
            "Status": {"select": {"name": STATUS_LABELS[status]}},
            "Gate resource": {"url": gate_resource_url},
        },
    )


def set_core_source(creator_page_id: str, is_core: bool) -> None:
    """BUILD_SPEC 3.3: creator save-count >= 5 -> Core source checkbox."""
    client = _client()
    client.pages.update(page_id=creator_page_id, properties={"Core source": {"checkbox": is_core}})