"""Notion → Obsidian vault sync (DATA_SCHEMA.md §2, built as the local smart-memory
layer). Local-only: run via scripts/sync_to_obsidian.py, never deployed to Render.

One markdown note per save at {VAULT_PATH}/reels/{date}-{shortcode}.md with YAML
frontmatter and [[wikilinks]]; topic/creator notes carrying a real, auto-regenerated
index of their reels (not just a bare stub relying on Obsidian's Backlinks panel); a
"## Related" section built from the sqlite-vec embeddings computed at capture time
(NOT recomputed); and a vault-root _index.md with real per-topic previews. Idempotent —
existing notes are matched by the shortcode in their frontmatter and rewritten in place.

Topic/creator notes and _index.md carry an AUTO-GENERATED block (see upsert_auto_block)
so a user's own notes above it survive every re-sync untouched.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional

from app import notion_writer, store
from app.topic_descriptions import TOPIC_DESCRIPTIONS

logger = logging.getLogger("reelbrain.obsidian")

VAULT_PATH = os.environ.get("VAULT_PATH", r"C:\Users\garvb\ReelBrainVault").strip()

RELATED_TOP_K = 3
FRONTMATTER_SHORTCODE_RE = re.compile(r"^shortcode:\s*(\S+)\s*$", re.MULTILINE)
# scripts/ingest_resources.py writes these two frontmatter lines on every
# resources/*.md note -- topics_plain is a comma-separated convenience
# duplicate of the wikilink topics list, so linking resources into topic
# indexes here doesn't need a full YAML parser for one extra field.
FRONTMATTER_RESOURCE_SHORTCODE_RE = re.compile(r"^source_shortcode:\s*(\S+)\s*$", re.MULTILINE)
FRONTMATTER_RESOURCE_TOPICS_RE = re.compile(r"^topics_plain:\s*(.*)$", re.MULTILINE)

AUTO_START = "<!-- AUTO-GENERATED, DO NOT EDIT BELOW -->"
AUTO_END = "<!-- END AUTO-GENERATED -->"


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


def extract_main_point(blocks: list[dict]) -> str:
    """The reel's one-line Main Point is always the first top-level callout block
    (that's exactly what notion_writer._build_children emits it as)."""
    for block in blocks:
        if block.get("type") == "callout":
            return _rt_text(block.get("callout", {}).get("rich_text", []))
    return ""


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
    priority_select = _prop(props, "Priority").get("select") or {}
    date_prop = _prop(props, "Posted at").get("date") or {}
    posted = (date_prop.get("start") or "")[:10]
    return {
        "page_id": page.get("id", ""),
        "shortcode": _rt_text(_prop(props, "Shortcode").get("rich_text", [])),
        "title": _rt_text(_prop(props, "Title").get("title", [])),
        "status": select.get("name", ""),
        "value_score": value_select.get("name", ""),
        # Plain text ("High"/"Medium"/"Low") straight from the Priority Select
        # property — no emoji, ever. Empty string for rows saved before the
        # Priority property existed (see PROGRESS.md: backfill is a separate,
        # not-yet-built script).
        "priority": priority_select.get("name", ""),
        "topics": [t["name"] for t in _prop(props, "Topics").get("multi_select", [])],
        "url": _prop(props, "Reel URL").get("url") or "",
        "posted": posted or (page.get("created_time", "") or "")[:10],
        "gate_resource": _prop(props, "Gate resource").get("url") or "",
        "suggested_action": _rt_text(_prop(props, "Suggested action").get("rich_text", [])),
        "plain_summary": _rt_text(_prop(props, "Plain summary").get("rich_text", [])),
    }


# Reel note filenames are the main_point itself (slugified), not date-shortcode
# -- so Obsidian's graph view shows readable node names. See Task 2,
# PROGRESS.md, and scripts/rename_reel_notes.py (the one-time migration for
# notes written before this convention).
MAIN_POINT_SLUG_MAX_LEN = 60


def slugify_main_point(main_point: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", (main_point or "").strip().lower()).strip("-")
    slug = slug[:MAIN_POINT_SLUG_MAX_LEN].rstrip("-")
    return slug or "untitled"


def note_filename(fields: dict, used_slugs: Optional[set[str]] = None) -> str:
    """Slugified main_point, max MAIN_POINT_SLUG_MAX_LEN chars. If that slug is
    already taken (by a DIFFERENT save, tracked via used_slugs across a sync
    pass), the shortcode is appended to disambiguate -- two reels can genuinely
    share a near-identical main_point."""
    slug = slugify_main_point(fields["title"])
    if used_slugs is not None:
        if slug in used_slugs:
            slug = f"{slug}-{fields['shortcode'].lower()}"
        used_slugs.add(slug)
    return f"{slug}.md"


def build_note(fields: dict, creator: Optional[str], body_markdown: str,
               related_stems: list[str], resource_stem: Optional[str] = None) -> str:
    lines = ["---"]
    lines.append(f"shortcode: {fields['shortcode']}")
    if creator:
        lines.append(f'creator: "[[creators/{_slugify(creator)}]]"')
    lines.append(f'status: "{fields["status"]}"')
    if fields["priority"]:
        lines.append(f"priority: {fields['priority']}")
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
    # The zero-context explanation goes FIRST, before priority/score, so the
    # note makes sense to someone who has never seen the reel (Phase C).
    plain = fields.get("plain_summary", "")
    if plain:
        lines.append(f"> {plain}")
        lines.append("")
    # Plain-text Priority/Score lines, no emoji — visible immediately on
    # opening the note, not just buried in frontmatter YAML.
    if fields["priority"]:
        lines.append(f"Priority: {fields['priority']}")
    if fields["value_score"]:
        lines.append(f"Score: {fields['value_score']}")
    if fields["priority"] or fields["value_score"]:
        lines.append("")
    # The one imperative next step, right under the header where it's seen
    # immediately — skipped entirely when there's nothing to do.
    action = fields.get("suggested_action", "")
    if action and action.lower() != "none — informational":
        lines.append("## Do")
        lines.append("")
        lines.append(action)
        lines.append("")
    lines.append(body_markdown.rstrip())
    if resource_stem:
        # scripts/ingest_resources.py writes the actual resources/*.md note;
        # this just links to it, so a re-sync always reflects the current
        # linkage without ingest_resources.py needing to touch reel notes
        # directly (which sync() fully overwrites every run anyway).
        lines.append("")
        lines.append("## Attached Resource")
        lines.append("")
        lines.append(f"- [[resources/{resource_stem}]]")
    if related_stems:
        lines.append("")
        lines.append("## Related")
        lines.append("")
        for stem in related_stems:
            lines.append(f"- [[reels/{stem}]]")
    return "\n".join(lines).rstrip() + "\n"


# --- auto-generated block (preserves user edits above/below it) ----------------


def upsert_auto_block(path: Path, default_header: str, generated_lines: list[str]) -> None:
    """Rewrite ONLY the content between AUTO_START/AUTO_END, leaving everything
    else in the file untouched. This is how topic/creator stubs and _index.md
    survive re-syncs: a user's own "## Notes" section (or any preamble) written
    above the markers is never touched, and anything they happen to add below
    the block is preserved too. If the file doesn't exist yet, or exists without
    markers (an old-style bare stub from before this feature), default_header
    seeds the content above the block.
    """
    if path.exists():
        content = path.read_text(encoding="utf-8")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = default_header

    start_idx = content.find(AUTO_START)
    end_idx = content.find(AUTO_END)
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        prefix = content[:start_idx].rstrip("\n")
        suffix = content[end_idx + len(AUTO_END):].lstrip("\n").rstrip("\n")
    else:
        prefix = content.rstrip("\n")
        suffix = ""

    block = "\n".join([AUTO_START, *generated_lines, AUTO_END])
    parts = [p for p in (prefix, block) if p]
    new_content = "\n\n".join(parts)
    if suffix:
        new_content += "\n\n" + suffix
    path.write_text(new_content.rstrip("\n") + "\n", encoding="utf-8")


def _sort_entries(entries: list[dict]) -> list[dict]:
    """value_score descending, then posted date descending. Missing value_score
    sorts as 0 (last among scored reels); missing date sorts last among ties."""
    return sorted(entries, key=lambda e: (e["value_score"] or 0, e["posted"] or ""), reverse=True)


def _format_entry_line(entry: dict) -> str:
    folder = entry.get("folder", "reels")
    if folder == "resources":
        # Resources don't carry priority/value_score (that's a reel-extraction
        # concept) -- a lighter line marks them distinctly instead of printing
        # placeholder dashes for fields that don't apply.
        return f"- 📄 [[resources/{entry['stem']}|{entry['title']}]] — {entry.get('main_point') or 'attached resource'}"
    priority_part = f"Priority: {entry['priority']}" if entry.get("priority") else "Priority: —"
    score_part = f"Score: {entry['value_score']}" if entry["value_score"] is not None else "Score: —"
    # Phase C: the listing text is the zero-context explanation when we have
    # one -- a topic page should be readable by someone who's never seen any
    # of these reels. Falls back to main_point for rows not yet backfilled.
    summary = entry.get("plain_summary") or entry["main_point"] or "(no main point)"
    return f"- [[{folder}/{entry['stem']}|{entry['title']}]] — {priority_part} — {score_part} — {summary}"


def write_stub_index(vault: Path, folder: str, name: str, entries: list[dict]) -> Path:
    """topics/x.md or creators/x.md: a real "## Saved Reels" index of every reel
    tagged with this topic/creator, regenerated every sync. Sorted value desc,
    then date desc. Anything the user wrote above this block survives."""
    path = vault / folder / f"{_slugify(name)}.md"
    # Phase C: a plain-English statement of what this category actually covers,
    # so a topic page means something to someone with zero context. Lives INSIDE
    # the auto block (regenerated every sync), not just in default_header, which
    # only applies the first time the file is created.
    description = TOPIC_DESCRIPTIONS.get(name, "")
    default_header = f"# {name}\n\n## Notes\n"
    lines: list[str] = []
    if description:
        lines += ["### What this covers", "", description, ""]
    lines += ["## Saved Reels", ""]
    lines.extend(_format_entry_line(e) for e in _sort_entries(entries))
    upsert_auto_block(path, default_header, lines)
    return path


PRIORITY_ORDER = ["High", "Medium", "Low"]


def write_topics_index(vault: Path, topic_entries: dict[str, list[dict]]) -> None:
    """_index.md: grouped by Priority FIRST (## High Priority, ## Medium
    Priority, ## Low Priority), each section listing every topic that has at
    least one reel at that tier with a count — so opening the vault
    immediately shows what needs attention, not just a topic dump sorted by
    volume. Reels with no Priority set (saved before this property existed)
    fall into "Low Priority" rather than vanishing from the index."""
    by_priority: dict[str, dict[str, int]] = {p: {} for p in PRIORITY_ORDER}
    for topic, entries in topic_entries.items():
        for entry in entries:
            priority = entry.get("priority") or "Low"
            by_priority.setdefault(priority, {})
            by_priority[priority][topic] = by_priority[priority].get(topic, 0) + 1

    lines: list[str] = []
    for priority in PRIORITY_ORDER:
        topics = by_priority.get(priority, {})
        lines.append(f"## {priority} Priority")
        lines.append("")
        if not topics:
            lines.append("(none)")
        else:
            for topic, count in sorted(topics.items(), key=lambda kv: (-kv[1], kv[0])):
                plural = "save" if count == 1 else "saves"
                lines.append(f"- [[topics/{_slugify(topic)}|{topic}]] — {count} {plural}")
        lines.append("")
    if not topic_entries:
        lines = ["(no topics yet — run a few captures first)"]
    upsert_auto_block(vault / "_index.md", "# Topics Index\n", lines)


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


def existing_resource_notes(vault: Path) -> dict[str, dict]:
    """shortcode -> {"stem", "topics"} for every resources/*.md note written by
    scripts/ingest_resources.py, read from its source_shortcode/topics_plain
    frontmatter -- lets sync() link reel notes to their attached resource and
    fold resources into topic indexes without ingest_resources.py needing to
    touch reel/topic notes directly (sync() fully regenerates those anyway)."""
    mapping: dict[str, dict] = {}
    resources_dir = vault / "resources"
    if not resources_dir.is_dir():
        return mapping
    for path in resources_dir.glob("*.md"):
        try:
            head = path.read_text(encoding="utf-8")[:1000]
        except OSError:
            continue
        match = FRONTMATTER_RESOURCE_SHORTCODE_RE.search(head)
        if not match:
            continue
        topics_match = FRONTMATTER_RESOURCE_TOPICS_RE.search(head)
        topics = [t.strip() for t in (topics_match.group(1).split(",") if topics_match else []) if t.strip()]
        mapping[match.group(1)] = {"stem": path.stem, "topics": topics}
    return mapping


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
    used_slugs = {path.stem for path in existing.values()}
    path_by_shortcode: dict[str, Path] = {}
    for fields in all_fields:
        path_by_shortcode[fields["shortcode"]] = existing.get(
            fields["shortcode"], vault / "reels" / note_filename(fields, used_slugs)
        )

    resource_notes = existing_resource_notes(vault)

    # pass 2: bodies, related links, writes — and collect per-topic/creator entries
    topic_entries: dict[str, list[dict]] = {}
    creator_entries: dict[str, list[dict]] = {}
    written = 0
    for fields in all_fields:
        shortcode = fields["shortcode"]
        row = store.get_by_shortcode(shortcode)
        creator = row["creator"] if row and row["creator"] else None

        blocks = _fetch_blocks(client, fields["page_id"])
        body = blocks_to_markdown(client, blocks)
        main_point = extract_main_point(blocks)
        related_stems = [
            path_by_shortcode[sc].stem
            for sc in related_shortcodes(shortcode)
            if sc in path_by_shortcode
        ]
        resource_stem = resource_notes.get(shortcode, {}).get("stem")

        note = build_note(fields, creator, body, related_stems, resource_stem)
        path_by_shortcode[shortcode].write_text(note, encoding="utf-8")
        written += 1

        raw_score = fields["value_score"]
        entry = {
            "stem": path_by_shortcode[shortcode].stem,
            "title": fields["title"] or shortcode,
            "value_score": int(raw_score) if str(raw_score).isdigit() else None,
            "posted": fields["posted"],
            "main_point": main_point,
            "plain_summary": fields.get("plain_summary", ""),
            "priority": fields["priority"],
        }

        if creator:
            creator_entries.setdefault(creator, []).append(entry)
        for topic in fields["topics"]:
            topic_entries.setdefault(topic, []).append(entry)

    # Resources fold into the SAME per-topic indexes as reels (marked 📄), so
    # browsing a topic surfaces the attached resource right alongside the
    # reels that reference it -- not a separate, easy-to-miss index.
    for shortcode, info in resource_notes.items():
        resource_entry = {
            "stem": info["stem"],
            "title": info["stem"],
            "folder": "resources",
            "value_score": None,
            "posted": "",
            "main_point": "attached resource",
            "priority": None,
        }
        for topic in info["topics"]:
            topic_entries.setdefault(topic, []).append(resource_entry)

    for topic, entries in topic_entries.items():
        write_stub_index(vault, "topics", topic, entries)
    for creator, entries in creator_entries.items():
        write_stub_index(vault, "creators", creator, entries)

    write_topics_index(vault, topic_entries)
    return {"notes_written": written, "topics": len(topic_entries), "vault": str(vault)}
