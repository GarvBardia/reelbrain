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
    "archived": "🗄 Archived",
    # Photo/carousel posts: yt-dlp is video-only and can never fetch these
    "photo_manual": "📷 Photo — manual",
}
# Reverse lookup for reading a page's Status back into our internal keys (used by
# the ephemeral-disk-recovery fallback below). "✅ Processed/Reviewed" is a
# manual-only status the pipeline never writes, so it has no internal key.
_STATUS_LABELS_REVERSE = {label: key for key, label in STATUS_LABELS.items()}

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


# --- reading pages back (ephemeral-disk recovery for /attach and /retry) -------
#
# SQLite is the normal source of truth for the app's own bookkeeping, but Render's
# free tier wipes its ephemeral disk on every redeploy/restart. Notion is the
# durable copy. When a local row is missing, store.py queries Notion directly via
# the functions below and re-persists whatever it finds locally as a byproduct.


def _rt_text(rich_text_items: Optional[list[dict]]) -> str:
    """Concatenate a rich_text (or title) array read back from the API. Real
    responses carry plain_text; payloads built by tests carry text.content —
    accept both, mirroring app/obsidian_sync.py's reader of the same shape."""
    parts = []
    for item in rich_text_items or []:
        parts.append(item.get("plain_text") or item.get("text", {}).get("content", ""))
    return "".join(parts)


def status_label_from_notion(label: str) -> Optional[str]:
    return _STATUS_LABELS_REVERSE.get(label)


def extract_saves_fields(page: dict) -> dict:
    """The subset of a raw Saves-DB page's properties that store.py's fallback
    needs to reconstruct a usable local row: shortcode, permalink, note, status,
    gate keyword, title, plus the page's own id/url."""
    props = page.get("properties", {})
    shortcode = _rt_text((props.get("Shortcode") or {}).get("rich_text"))
    note = _rt_text((props.get("My note") or {}).get("rich_text")) or None
    gate_keyword = _rt_text((props.get("Gate keyword") or {}).get("rich_text")) or None
    permalink = (props.get("Reel URL") or {}).get("url") or ""
    status_label = ((props.get("Status") or {}).get("select") or {}).get("name", "")
    title = _rt_text((props.get("Title") or {}).get("title"))
    return {
        "shortcode": shortcode,
        "permalink": permalink,
        "note": note,
        "status_label": status_label,
        "gate_keyword": gate_keyword,
        "title": title,
        "page_id": page.get("id", ""),
        "url": page.get("url", ""),
    }


def find_page_by_shortcode(shortcode: str) -> Optional[dict]:
    """Exact Shortcode match in the Saves data source, or None. Used by /retry's
    fallback when the local SQLite row is missing."""
    client = _client()
    saves_ds_id = _resolve_data_source_id(client, NOTION_DB_ID)
    results = client.data_sources.query(
        data_source_id=saves_ds_id,
        filter={"property": "Shortcode", "rich_text": {"equals": shortcode}},
    )["results"]
    return results[0] if results else None


def find_awaiting_dm_pages() -> list[dict]:
    """Every Saves page with Status = Awaiting DM, most-recently-edited first.
    Used by /attach's fallback when no local SQLite row matches; store.py applies
    the same shortcode-then-note-then-most-recent priority as its local search."""
    client = _client()
    saves_ds_id = _resolve_data_source_id(client, NOTION_DB_ID)
    return client.data_sources.query(
        data_source_id=saves_ds_id,
        filter={"property": "Status", "select": {"equals": STATUS_LABELS["awaiting_dm"]}},
        sorts=[{"timestamp": "last_edited_time", "direction": "descending"}],
    )["results"]


def find_saves_pages_since(created_on_or_after_iso: str) -> list[dict]:
    """Every Saves page created on/after the given ISO timestamp, newest first,
    fully paginated. Used by the digests (FIX 2, see PROGRESS.md): Render's
    ephemeral disk wipes local SQLite on every redeploy, which made digests
    report false 'nothing saved' days — Notion is the durable source, so the
    digest windows now come from here."""
    client = _client()
    saves_ds_id = _resolve_data_source_id(client, NOTION_DB_ID)
    pages: list[dict] = []
    cursor: Optional[str] = None
    while True:
        kwargs: dict = {
            "data_source_id": saves_ds_id,
            "filter": {
                "timestamp": "created_time",
                "created_time": {"on_or_after": created_on_or_after_iso},
            },
            "sorts": [{"timestamp": "created_time", "direction": "descending"}],
        }
        if cursor:
            kwargs["start_cursor"] = cursor
        response = client.data_sources.query(**kwargs)
        pages.extend(response["results"])
        if not response.get("has_more"):
            return pages
        cursor = response.get("next_cursor")


def extract_digest_fields(page: dict) -> dict:
    """The digest-relevant subset of a Saves page's properties. Title is the
    extraction's main_point (that's exactly what _build_properties writes), so
    it doubles as the digest entry's summary line."""
    props = page.get("properties", {})
    return {
        "shortcode": _rt_text((props.get("Shortcode") or {}).get("rich_text")),
        "title": _rt_text((props.get("Title") or {}).get("title")),
        "topics": [t["name"] for t in (props.get("Topics") or {}).get("multi_select", [])],
        "priority": (((props.get("Priority") or {}).get("select")) or {}).get("name", ""),
        "value_score": (((props.get("Value score") or {}).get("select")) or {}).get("name", ""),
        "status_label": (((props.get("Status") or {}).get("select")) or {}).get("name", ""),
        "permalink": (props.get("Reel URL") or {}).get("url") or "",
        "page_url": page.get("url", ""),
    }


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
        props["Priority"] = {"select": {"name": extraction.priority}}
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
        if extraction.research_context:
            # Second-pass, search-grounded writeups (gemini_pipe.run_research_context)
            # -- rendered as body blocks, never a single property: each entry is
            # short (2-3 sentences), but several entries concatenated into one
            # rich_text value could exceed Notion's 2000-char-per-rich_text-object
            # cap (confirmed against developers.notion.com/reference/request-limits).
            # A toggle (same mechanism as Transcript/Raw caption below) gives each
            # item its own paragraph block, and app/obsidian_sync.py's generic
            # any-titled-toggle handling already renders this as its own
            # "## Research Context" section with no further code needed there.
            blocks.append({
                "object": "block", "type": "toggle",
                "toggle": {
                    "rich_text": _rich_text("Research Context"),
                    "children": [
                        {
                            "object": "block", "type": "paragraph",
                            "paragraph": {"rich_text": _rich_text(f"{item.topic}: {item.context}")},
                        }
                        for item in extraction.research_context
                    ],
                },
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


def find_child_page_by_title(client, parent_page_id: str, title: str) -> Optional[str]:
    """Look for a direct child page of parent_page_id with this exact title
    (paginated). Returns its page_id, or None if it doesn't exist yet.
    Backs the single-persistent-page pattern (digests) below."""
    cursor = None
    while True:
        kwargs = {"block_id": parent_page_id}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = client.blocks.children.list(**kwargs)
        for block in resp.get("results", []):
            if block.get("type") == "child_page" and block.get("child_page", {}).get("title") == title:
                return block["id"]
        if not resp.get("has_more"):
            return None
        cursor = resp.get("next_cursor")


def upsert_named_page(parent_page_id: str, title: str, children: list[dict]) -> dict:
    """Single-persistent-page pattern: finds a direct child of parent_page_id
    with this exact title and REPLACES its body blocks (same delete-then-append
    pattern as update_page above), instead of creating a new dated page every
    run — which would accumulate endlessly (see PROGRESS.md, digest fix).
    Creates the page once, on the first call, if it doesn't exist yet."""
    client = _client()
    children = children[:100]
    page_id = find_child_page_by_title(client, parent_page_id, title)
    if page_id is None:
        page = client.pages.create(
            parent={"type": "page_id", "page_id": parent_page_id},
            properties={"title": {"title": _rich_text(title)}},
            children=children,
        )
        return {"page_id": page["id"], "url": page["url"]}

    existing = client.blocks.children.list(block_id=page_id)["results"]
    for block in existing:
        client.blocks.delete(block_id=block["id"])
    client.blocks.children.append(block_id=page_id, children=children)
    page = client.pages.update(page_id=page_id, properties={"title": {"title": _rich_text(title)}})
    return {"page_id": page["id"], "url": page["url"]}


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