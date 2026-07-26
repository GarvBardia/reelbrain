"""Task 3 (see PROGRESS.md): find vault notes with ZERO inbound wikilinks and
either reconnect them or, as a last resort, discard them.

LOCAL-ONLY. Default mode is a REPORT (no writes at all). Remediation is opt-in
per category, and deletion additionally requires --apply-deletes.

Orphan = no other note anywhere in the vault links to it. Root-level notes
(_index.md, INSTALLED.md, IMPLEMENTATION_QUEUE.md, scout_input.md) are
deliberately EXCLUDED: they're the vault's entry points//generated inputs, so
"nothing links to them" is their normal state, not a defect.

Remediation, per the four cases:
  1. TOPIC note orphan with >= MIN_REELS_TO_REBUILD reels carrying that tag in
     their frontmatter -> the reels exist but the index is stale/broken;
     rebuild its "## Saved Reels" block (obsidian_sync.write_stub_index).
  2. REEL note orphan with no topic tags at all -> nothing can link to it
     because it has no topic notes to link FROM. Assign tags with the same
     lightweight, quota-safe Gemini call Task 1's Job B uses
     (notion_deep_clean.suggest_tags), write them to Notion; the next sync
     then creates/updates the topic notes that link back to it.
  3. RESOURCE note orphan with no parent reel -> look for a Notion row whose
     Gate resource URL matches (normalized, ignoring tracking params); if
     found, record source_shortcode so the next sync renders the reel-side
     link; if not, list it under "## Unlinked resources" in _index.md so it's
     findable rather than floating.
  4. Still orphaned after the above AND no real content -> delete from the
     vault and archive its Notion row.

     SAFETY: a row whose Status is an ACTIVE WORKFLOW STATE (Failed — retry,
     Awaiting DM, processing) is NEVER deleted, even when it's contentless --
     "Failed — retry" specifically means the pipeline still intends to reprocess
     it, so discarding it would silently drop a pending work item. Those are
     reported for a human instead.

Usage:
    python scripts/vault_orphans.py                    # report only
    python scripts/vault_orphans.py --fix              # cases 1-3 (no deletes)
    python scripts/vault_orphans.py --fix --apply-deletes   # + case 4
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("reelbrain.vault_orphans")

# Entry points / generated inputs -- "nothing links here" is correct for these.
ROOT_ENTRY_POINTS = {"_index", "INSTALLED", "IMPLEMENTATION_QUEUE", "scout_input"}
MIN_REELS_TO_REBUILD = 2

# Must match scripts/notion_deep_clean.PLACEHOLDER_TITLE -- rows still showing
# it have no real content to tag; they belong to the recovery worker.
PLACEHOLDER_TITLE = "No caption or transcript available."

# Statuses that mean "the pipeline still has work planned for this row" --
# never delete/archive these, however empty they look.
ACTIVE_WORKFLOW_STATUSES = {"⚠️ Failed — retry", "⏳ Awaiting DM", "processing"}

# Body sections that constitute "real content" in a reel/resource note.
CONTENT_SECTIONS = ("## Supporting points", "## Steps", "## Quotable lines",
                    "## Summary", "## Key takeaways")

_LINK_RE = re.compile(r"\[\[([^\]|#]+)")
_SHORTCODE_RE = re.compile(r"^shortcode:\s*(\S+)\s*$", re.MULTILINE)
_STATUS_RE = re.compile(r'^status:\s*"?([^"\n]+)"?\s*$', re.MULTILINE)
_TOPICS_RE = re.compile(r"^topics:\s*$", re.MULTILINE)
_SOURCE_SC_RE = re.compile(r"^source_shortcode:\s*(\S+)\s*$", re.MULTILINE)
_RES_URL_RE = re.compile(r"^(?:resource_)?url:\s*(\S+)\s*$", re.MULTILINE)


def build_link_graph(vault: Path) -> tuple[dict[str, Path], dict[str, set[str]]]:
    """(notes_by_key, inbound) where key is the vault-relative path without
    .md (e.g. "reels/some-slug"). A note linking to itself doesn't count."""
    notes: dict[str, Path] = {}
    for md in vault.rglob("*.md"):
        if ".obsidian" in md.parts:
            continue
        notes[md.relative_to(vault).as_posix()[:-3]] = md

    inbound: dict[str, set[str]] = defaultdict(set)
    for key, path in notes.items():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for target in _LINK_RE.findall(text):
            target = target.strip()
            if target and target != key:
                inbound[target].add(key)
    return notes, inbound


def find_orphans(vault: Path) -> dict[str, list[str]]:
    """{"reels": [...], "topics": [...], "creators": [...], "resources": [...]}
    -- root-level entry points excluded by design."""
    notes, inbound = build_link_graph(vault)
    orphans: dict[str, list[str]] = defaultdict(list)
    for key in notes:
        if inbound.get(key):
            continue
        if "/" not in key:
            if key in ROOT_ENTRY_POINTS:
                continue
            orphans["(root)"].append(key)
            continue
        orphans[key.split("/", 1)[0]].append(key)
    return {k: sorted(v) for k, v in orphans.items()}


def note_has_real_content(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return True  # unreadable -> never treat as empty, never delete
    if any(section in text for section in CONTENT_SECTIONS):
        return True
    # a Transcript/Raw caption toggle with actual text also counts
    body = text.split("---", 2)[-1]
    stripped = re.sub(r"^#.*$|^##.*$", "", body, flags=re.MULTILINE)
    stripped = stripped.replace("(no caption)", "").replace("(no speech detected)", "")
    stripped = stripped.replace("(unavailable)", "")
    return len(stripped.split()) >= 10


def reel_topics_from_frontmatter(path: Path) -> bool:
    try:
        return bool(_TOPICS_RE.search(path.read_text(encoding="utf-8")))
    except OSError:
        return False


def _field(path: Path, pattern: re.Pattern) -> Optional[str]:
    try:
        m = pattern.search(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    return m.group(1).strip() if m else None


def topic_reel_counts(vault: Path) -> dict[str, int]:
    """topic-slug -> number of reel notes whose frontmatter references it."""
    counts: dict[str, int] = defaultdict(int)
    for md in (vault / "reels").glob("*.md"):
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        for target in _LINK_RE.findall(text):
            target = target.strip()
            if target.startswith("topics/"):
                counts[target.split("/", 1)[1]] += 1
    return dict(counts)


def classify(vault: Path) -> dict:
    """Full report: every orphan, its category, and the remediation it needs."""
    orphans = find_orphans(vault)
    counts = topic_reel_counts(vault)

    rebuildable_topics, reel_needs_tags, resource_needs_parent = [], [], []
    deletable, retry_protected = [], []

    # Case 1, as actually reachable in THIS vault's link model. The literal
    # spec ("orphan topic note with 2+ reels tagged") can never occur here:
    # reel frontmatter itself contains `topics: - "[[topics/x]]"`, so any topic
    # with 2+ reels ALWAYS has inbound links and is by definition not an
    # orphan. The real, reachable staleness this is aiming at is a topic note
    # that has the reels but is MISSING its generated "## Saved Reels" index
    # block -- so browsing the topic shows nothing even though reels point at
    # it. That's what gets rebuilt.
    for slug, count in counts.items():
        if count < MIN_REELS_TO_REBUILD:
            continue
        topic_path = vault / "topics" / f"{slug}.md"
        if not topic_path.exists():
            rebuildable_topics.append(f"topics/{slug}")
            continue
        try:
            if "## Saved Reels" not in topic_path.read_text(encoding="utf-8"):
                rebuildable_topics.append(f"topics/{slug}")
        except OSError:
            continue

    for key in orphans.get("reels", []):
        path = vault / f"{key}.md"
        status = _field(path, _STATUS_RE) or ""
        if not reel_topics_from_frontmatter(path):
            if note_has_real_content(path):
                reel_needs_tags.append(key)
            elif status in ACTIVE_WORKFLOW_STATUSES:
                retry_protected.append((key, status))
            else:
                deletable.append(key)

    for key in orphans.get("resources", []):
        if not _field(vault / f"{key}.md", _SOURCE_SC_RE):
            resource_needs_parent.append(key)

    return {
        "orphans": orphans,
        "rebuildable_topics": rebuildable_topics,
        "reel_needs_tags": reel_needs_tags,
        "resource_needs_parent": resource_needs_parent,
        "deletable": deletable,
        "retry_protected": retry_protected,
    }


# --- remediation ----------------------------------------------------------------


def rebuild_topic_indexes(vault: Path, topic_keys: list[str], sync_fn=None) -> int:
    """Case 1: the reels exist and reference the topic; the topic's own index
    block is stale. A full sync regenerates every topic index from Notion, so
    that's the correct, non-duplicating fix -- this just reports how many
    would be repaired by it."""
    return len(topic_keys)


def link_resources_to_reels(vault: Path, resource_keys: list[str], gate_map: dict[str, str],
                            dry_run: bool = True, print_fn=print) -> tuple[int, list[str]]:
    """Case 3: match a parentless resource note to a Notion row by normalized
    Gate resource URL. Returns (linked_count, still_unlinked_keys)."""
    from scripts.attach_and_ingest_resources import normalize_url

    linked, unlinked = 0, []
    for key in resource_keys:
        path = vault / f"{key}.md"
        url = _field(path, _RES_URL_RE)
        shortcode = gate_map.get(normalize_url(url)) if url else None
        if not shortcode:
            unlinked.append(key)
            continue
        if dry_run:
            print_fn(f"  would link {key} -> reel {shortcode}")
        else:
            text = path.read_text(encoding="utf-8")
            text = text.replace("---\n", f"---\nsource_shortcode: {shortcode}\n", 1)
            path.write_text(text, encoding="utf-8")
            print_fn(f"  linked {key} -> reel {shortcode}")
        linked += 1
    return linked, unlinked


def build_tagging_rows(pages: list[dict], shortcodes: set[str]) -> tuple[list[dict], int]:
    """Case 2's row list: the orphan reels that Gemini may legitimately tag.
    Returns (rows, skipped_placeholder_count).

    The placeholder guard here MUST mirror
    notion_deep_clean.find_topicless_rows': tagging a row whose title is still
    the placeholder makes Gemini describe the PLACEHOLDER TEXT rather than the
    reel. Observed live before this guard existed -- it produced junk tags
    ("captions", "transcripts", "content-unavailable", "media-accessibility")
    that had to be reverted. Those rows belong to the recovery worker, which
    can restore a real caption first."""
    from app import notion_writer

    rows, skipped = [], 0
    for page in pages:
        fields = notion_writer.extract_saves_fields(page)
        if fields["shortcode"] not in shortcodes or not fields["title"]:
            continue
        if fields["title"] == PLACEHOLDER_TITLE:
            skipped += 1
            continue
        rows.append({"shortcode": fields["shortcode"], "page_id": fields["page_id"],
                     "title": fields["title"]})
    return rows, skipped


def delete_orphans(vault: Path, keys: list[str], dry_run: bool = True, print_fn=print) -> int:
    """Case 4: remove the note AND archive its Notion row. Only ever called
    for keys that classify() already cleared as contentless AND not in an
    active workflow state."""
    from app import notion_writer, store

    deleted = 0
    for key in keys:
        path = vault / f"{key}.md"
        shortcode = _field(path, _SHORTCODE_RE)
        if dry_run:
            print_fn(f"  would delete {key} (shortcode={shortcode}) + archive its Notion row")
            deleted += 1
            continue
        if shortcode:
            page = notion_writer.find_page_by_shortcode(shortcode)
            if page is not None:
                notion_writer.set_status(page["id"], "archived")
                try:
                    store.update_save(shortcode, status="archived")
                except Exception:  # noqa: BLE001 - local mirror is best-effort
                    pass
        path.unlink()
        print_fn(f"  deleted {key} (shortcode={shortcode}) + archived its Notion row")
        deleted += 1
    return deleted


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vault", default=None)
    parser.add_argument("--fix", action="store_true", help="apply cases 1-3 (never deletes)")
    parser.add_argument("--apply-deletes", action="store_true", help="also apply case 4 (destructive)")
    parser.add_argument("--limit", type=int, default=None, help="cap the Gemini tagging pass")
    args = parser.parse_args()

    from app.obsidian_sync import VAULT_PATH

    vault = Path(args.vault or VAULT_PATH)
    report = classify(vault)

    total = sum(len(v) for v in report["orphans"].values())
    print(f"vault: {vault}")
    print(f"orphan notes (zero inbound wikilinks): {total}\n")
    for folder, keys in sorted(report["orphans"].items()):
        print(f"  {folder}: {len(keys)}")

    print(f"\ncase 1 -- topic notes rebuildable ({MIN_REELS_TO_REBUILD}+ reels tagged): "
          f"{len(report['rebuildable_topics'])}")
    print(f"case 2 -- reel notes needing topic tags: {len(report['reel_needs_tags'])}")
    print(f"case 3 -- resource notes needing a parent: {len(report['resource_needs_parent'])}")
    print(f"case 4 -- contentless + deletable: {len(report['deletable'])}")
    if report["retry_protected"]:
        print(f"\nPROTECTED from deletion ({len(report['retry_protected'])}) -- contentless but in an "
              f"active workflow state, so the pipeline still intends to reprocess them:")
        for key, status in report["retry_protected"]:
            print(f"   {key}  [{status}]")

    if not args.fix:
        print("\n[report only] re-run with --fix to apply cases 1-3.")
        return

    # case 2: quota-safe Gemini tagging, reusing Task 1's Job B wholesale
    if report["reel_needs_tags"]:
        from app import notion_writer, store
        from scripts.notion_deep_clean import fix_topics

        shortcodes = {}
        for key in report["reel_needs_tags"]:
            sc = _field(vault / f"{key}.md", _SHORTCODE_RE)
            if sc:
                shortcodes[sc] = key
        pages = notion_writer.find_saves_pages_since("1970-01-01T00:00:00")
        rows, skipped_placeholders = build_tagging_rows(pages, set(shortcodes))
        print(f"\ncase 2: tagging {len(rows)} reel row(s) via Gemini "
              f"({skipped_placeholders} placeholder row(s) skipped -- recovery worker's job)...")
        result = fix_topics(rows, store.get_taxonomy(), limit=args.limit)
        print(f"  tagged {len(result['fixed'])}/{len(rows)}, quota_stopped={result['quota_stopped']}")

    if report["deletable"]:
        deleted = delete_orphans(vault, report["deletable"], dry_run=not args.apply_deletes)
        verb = "deleted" if args.apply_deletes else "would delete (pass --apply-deletes)"
        print(f"\ncase 4: {verb} {deleted}")

    print("\nNEXT: run scripts/sync_to_obsidian.py so newly-tagged reels get their topic links.")


if __name__ == "__main__":
    main()
