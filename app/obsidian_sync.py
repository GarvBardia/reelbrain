"""Notion → Obsidian vault sync (DATA_SCHEMA.md §2, built as the local smart-memory
layer). Local-only: run via scripts/sync_to_obsidian.py, never deployed to Render.

One markdown note per save at {VAULT_PATH}/reels/{date}-{shortcode}.md with YAML
frontmatter and [[wikilinks]]; stub notes for creators/topics; a "## Related" section
built from the sqlite-vec embeddings computed at capture time (NOT recomputed); and a
vault-root _index.md over all topics. Idempotent — existing notes are matched by the
shortcode in their frontmatter and rewritten in place.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional

from app import notion_writer, store

logger = logging.getLogger("reelbrain.obsidian")

VAULT_PATH = os.environ.get("VAULT_PATH", r"C:\Users\garvb\ReelBrainVault").strip()

RELATED_TOP_K = 3
FRONTMATTER_SHORTCODE_RE = re.compile(r"^shortcode:\s*(\S+)\s*$", re.MULTILINE)


# --- Notion reading -----------------------------------------------------------


def _rt_text(rich_text_items: list[dict]) -> str:
    """Concatenate a rich_text array. Real API responses carry plain_text; payloads
    we built ourselves (and test fixtures) carry text.content — accept both."""
    parts = []
    for item in rich_text_items or []:
        parts.append(item.get("plain_text") or item.get("text", {}).get("content", ""))
    return "".join(parts)


def fetch_all_saves(client) -> list[dict]:
    """Every page in the Saves DB, paginated."""
    data_source_id = notion_writer._resolve_data_source_id(client, notion_writer.NOTION_DB_ID)
    pages: list[dict] = []
    cursor: Optional[str] = None
    while True:
        kwargs: dict = {"data_source_id": data_source_id}
        if cursor:
            kwargs["start_cursor"] = cursor
        response = client.data_sources.query(**kwargs)
        pages.extend(response["results"])
        if not response.get("has_more"):
            return pages
        cursor = response.get("next_cursor")


def _fetch_blocks(client, block_id: str) -> list[dict]:
    blocks: list[dict] = []
    cursor: Optional[str] = None
    while True:
        kwargs: dict = {"block_id": block_id}
        if cursor:
            kwargs["start_cursor"] = cursor
        response = client.blocks.children.list(**kwargs)
        blocks.extend(response["results"])
        if not response.get("has_more"):
            return blocks
        cursor = response.get("next_cursor")


# Section headings per block type — mirrors the page layout notion_writer emits.
_GROUP_HEADINGS = {
    "callout": "## Main point",
    "bulleted_list_item": "## Supporting points",
    "numbered_list_item": "## Steps",
    "bookmark": "## Resources",
    "paragraph": "## Resources",  # writer emits bare paragraphs only for URL-less resources
    "quote": "## Quotable lines",
}


def blocks_to_markdown(client, blocks: list[dict]) -> str:
    """Convert the known page layout (callout → bullets → numbered → bookmarks →
    quotes → toggles) to sectioned markdown. Unknown block types are skipped."""
    lines: list[str] = []
    current_heading: Optional[str] = None
    numbered_count = 0

    def _enter(heading: Optional[str]) -> None:
        nonlocal current_heading, numbered_count
        if heading and heading != current_heading:
            if lines:
                lines.append("")
            lines.append(heading)
            lines.append("")
            current_heading = heading
            numbered_count = 0

    for block in blocks:
        block_type = block.get("type", "")
        payload = block.get(block_type, {})
        if block_type == "callout":
            _enter(_GROUP_HEADINGS[block_type])
            lines.append(f"> {_rt_text(payload.get('rich_text', []))}")
        elif block_type == "bulleted_list_item":
            _enter(_GROUP_HEADINGS[block_type])
            lines.append(f"- {_rt_text(payload.get('rich_text', []))}")
        elif block_type == "numbered_list_item":
            _enter(_GROUP_HEADINGS[block_type])
            numbered_count += 1
            lines.append(f"{numbered_count}. {_rt_text(payload.get('rich_text', []))}")
        elif block_type == "bookmark":
            _enter(_GROUP_HEADINGS[block_type])
            lines.append(f"- <{payload.get('url', '')}>")
        elif block_type == "paragraph":
            _enter(_GROUP_HEADINGS[block_type])
            lines.append(f"- {_rt_text(payload.get('rich_text', []))}")
        elif block_type == "quote":
            _enter(_GROUP_HEADINGS[block_type])
            lines.append(f"> {_rt_text(payload.get('rich_text', []))}")
        elif block_type == "toggle":
            title = _rt_text(payload.get("rich_text", [])) or "Details"
            _enter(f"## {title}")
            children = payload.get("children")
            if children is None and block.get("has_children") and block.get("id"):
                children = _fetch_blocks(client, block["id"])
            for child in children or []:
                child_type = child.get("type", "")
                lines.append(_rt_text(child.get(child_type, {}).get("rich_text", [])))
    return "\n".join(lines).strip() + "\n"


# --- note building ------------------------------------------------------------


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", name.strip().lower()).strip("-")
    return slug or "unnamed"


def _prop(props: dict, name: str) -> dict:
    return props.get(name) or {}


def extract_note_fields(page: dict) -> dict:
    """Pull the frontmatter-relevant properties off a Notion page object."""
    props = page.get("properties", {})
    select = _prop(props, "Status").get("select") or {}
    value_select = _prop(props, "Value score").get("select") or {}
    date_prop = _prop(props, "Posted at").get("date") or {}
    posted = (date_prop.get("start") or "")[:10]
    return {
        "page_id": page.get("id", ""),
        "shortcode": _rt_text(_prop(props, "Shortcode").get("rich_text", [])),
        "title": _rt_text(_prop(props, "Title").get("title", [])),
        "status": select.get("name", ""),
        "value_score": value_select.get("name", ""),
        "topics": [t["name"] for t in _prop(props, "Topics").get("multi_select", [])],
        "url": _prop(props, "Reel URL").get("url") or "",
        "posted": posted or (page.get("created_time", "") or "")[:10],
    }


def note_filename(fields: dict) -> str:
    date = fields["posted"] or "undated"
    return f"{date}-{fields['shortcode']}.md"


def build_note(fields: dict, creator: Optional[str], body_markdown: str,
               related_stems: list[str]) -> str:
    lines = ["---"]
    lines.append(f"shortcode: {fields['shortcode']}")
    if creator:
        lines.append(f'creator: "[[creators/{_slugify(creator)}]]"')
    lines.append(f'status: "{fields["status"]}"')
    if fields["value_score"]:
        lines.append(f"value_score: {fields['value_score']}")
    if fields["topics"]:
        lines.append("topics:")
        for topic in fields["topics"]:
            lines.append(f'  - "[[topics/{_slugify(topic)}]]"')
    if fields["url"]:
        lines.append(f"url: {fields['url']}")
    if fields["posted"]:
        lines.append(f"posted: {fields['posted']}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {fields['title'] or fields['shortcode']}")
    lines.append("")
    lines.append(body_markdown.rstrip())
    if related_stems:
        lines.append("")
        lines.append("## Related")
        lines.append("")
        for stem in related_stems:
            lines.append(f"- [[reels/{stem}]]")
    return "\n".join(lines).rstrip() + "\n"


# --- vault filesystem ---------------------------------------------------------


def existing_notes_by_shortcode(vault: Path) -> dict[str, Path]:
    """Map frontmatter shortcode -> note path, so re-runs update in place even if
    the computed filename would differ (e.g. posted date filled in later)."""
    mapping: dict[str, Path] = {}
    reels_dir = vault / "reels"
    if not reels_dir.is_dir():
        return mapping
    for path in reels_dir.glob("*.md"):
        try:
            head = path.read_text(encoding="utf-8")[:500]
        except OSError:
            continue
        match = FRONTMATTER_SHORTCODE_RE.search(head)
        if match:
            mapping[match.group(1)] = path
    return mapping


def ensure_stub(vault: Path, folder: str, name: str) -> None:
    """topics/x.md or creators/x.md — created once so no wikilink dangles."""
    path = vault / folder / f"{_slugify(name)}.md"
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {name}\n\n## Notes\n", encoding="utf-8")


def write_topics_index(vault: Path, topic_counts: dict[str, int]) -> None:
    lines = ["# Topics Index", ""]
    for topic, count in sorted(topic_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        plural = "save" if count == 1 else "saves"
        lines.append(f"- [[topics/{_slugify(topic)}|{topic}]] — {count} {plural}")
    if len(lines) == 2:
        lines.append("(no topics yet — run a few captures first)")
    (vault / "_index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- related saves via capture-time embeddings ---------------------------------


def related_shortcodes(shortcode: str) -> list[str]:
    """Top-K most similar OTHER saves, from the embeddings already in sqlite-vec.
    Nothing is recomputed; a save with no stored vector simply gets no Related section."""
    vector = store.get_embedding(shortcode)
    if not vector:
        return []
    neighbors = store.find_neighbors(vector, k=RELATED_TOP_K + 1)
    return [sc for sc, _sim in neighbors if sc != shortcode][:RELATED_TOP_K]


# --- top-level sync -------------------------------------------------------------


def sync(vault_path: Optional[str] = None) -> dict:
    vault = Path(vault_path or VAULT_PATH)
    (vault / "reels").mkdir(parents=True, exist_ok=True)

    client = notion_writer._client()
    pages = fetch_all_saves(client)

    # pass 1: fields + filenames for every save, so Related links can resolve
    all_fields = [extract_note_fields(page) for page in pages]
    all_fields = [f for f in all_fields if f["shortcode"]]
    existing = existing_notes_by_shortcode(vault)
    path_by_shortcode: dict[str, Path] = {}
    for fields in all_fields:
        path_by_shortcode[fields["shortcode"]] = existing.get(
            fields["shortcode"], vault / "reels" / note_filename(fields)
        )

    # pass 2: bodies, related links, stubs, writes
    topic_counts: dict[str, int] = {}
    written = 0
    for fields in all_fields:
        shortcode = fields["shortcode"]
        row = store.get_by_shortcode(shortcode)
        creator = row["creator"] if row and row["creator"] else None

        body = blocks_to_markdown(client, _fetch_blocks(client, fields["page_id"]))
        related_stems = [
            path_by_shortcode[sc].stem
            for sc in related_shortcodes(shortcode)
            if sc in path_by_shortcode
        ]

        note = build_note(fields, creator, body, related_stems)
        path_by_shortcode[shortcode].write_text(note, encoding="utf-8")
        written += 1

        if creator:
            ensure_stub(vault, "creators", creator)
        for topic in fields["topics"]:
            ensure_stub(vault, "topics", topic)
            topic_counts[topic] = topic_counts.get(topic, 0) + 1

    write_topics_index(vault, topic_counts)
    return {"notes_written": written, "topics": len(topic_counts), "vault": str(vault)}
