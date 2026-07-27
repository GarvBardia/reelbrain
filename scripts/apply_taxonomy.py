"""Apply the approved taxonomy merges (TAXONOMY_PROPOSAL.md §5) to live Notion.

Order of operations, and why each step exists:
  1. Snapshot every row's tags + priority to JSON, for rollback. Written BEFORE
     any edit — if anything below goes wrong, this file is the undo.
  2. Apply the §2 merges: per affected row, rewrite Topics (remove merged-away
     tag, add the target if absent). Merges edit tags, never rows.
  3. HARD GATE: verify the merge changes NO row's computed priority. The
     proposal asserts zero priority drift; compute_priority(before) must equal
     compute_priority(after) for every changed row. If any row would drift, we
     STOP before touching Notion (in --apply the check runs on the plan first)
     and report exactly which row — that would mean the proposal's §3 impact
     analysis was wrong somewhere.
  4. Only if the gate passes: restructure the Obsidian index (handled by
     obsidian_sync's parent grouping) and delete topic notes orphaned by the
     merge — but only after checking each for user-written content.

Safe by default: runs as a DRY-RUN unless --apply is passed. Local-only (edits
the same Notion the rest of the tooling uses; needs the real token).

Usage:
    python scripts/apply_taxonomy.py            # dry-run: plan + priority gate
    python scripts/apply_taxonomy.py --apply    # actually edit Notion + vault
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app.taxonomy import MERGES, PROTECTED_TAGS, apply_merges

logger = logging.getLogger("reelbrain.apply_taxonomy")

SNAPSHOT_PREFIX = "taxonomy_snapshot_"


def _value_score_int(raw: str) -> int:
    """Value score is stored as a Select string ('1'..'5'); default 3 (the
    neutral tier) when missing, matching compute_priority's own middle rung."""
    return int(raw) if str(raw).isdigit() else 3


def build_rows(pages: list[dict]) -> list[dict]:
    """One dict per Saves row with exactly what the merge + gate need."""
    from app import notion_writer

    rows = []
    for page in pages:
        digest = notion_writer.extract_digest_fields(page)
        if not digest["shortcode"]:
            continue
        rows.append({
            "shortcode": digest["shortcode"],
            "page_id": page.get("id", ""),
            "topics": list(digest["topics"]),
            "priority": digest["priority"],
            "value_score": _value_score_int(digest["value_score"]),
        })
    return rows


def snapshot(rows: list[dict], path: Path) -> None:
    """Rollback file: every row's pre-merge tags/priority, plus a tag→rows map."""
    tag_to_rows: dict[str, list[str]] = {}
    for row in rows:
        for tag in row["topics"]:
            tag_to_rows.setdefault(tag, []).append(row["shortcode"])
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "merges": MERGES,
        "rows": [{k: row[k] for k in ("shortcode", "page_id", "topics", "priority", "value_score")}
                 for row in rows],
        "tag_to_rows": tag_to_rows,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def plan_merges(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Returns (changes, priority_violations).

    changes: rows whose tag set the merge actually alters, each with before/after
    topics and the computed before/after priority.
    priority_violations: any row where compute_priority(before) !=
    compute_priority(after) — the merge must never move a row's priority.
    """
    from app.gemini_pipe import compute_priority

    # Guardrail against a careless future edit to the merge map: none of the
    # deliberately-unmerged tags may appear as a merge source.
    illegal = PROTECTED_TAGS & set(MERGES)
    if illegal:
        raise ValueError(f"MERGES must never touch protected tags: {sorted(illegal)}")

    changes: list[dict] = []
    violations: list[dict] = []
    for row in rows:
        before = row["topics"]
        after = apply_merges(before)
        if before == after:
            continue
        pri_before = compute_priority(before, row["value_score"])
        pri_after = compute_priority(after, row["value_score"])
        change = {
            "shortcode": row["shortcode"],
            "page_id": row["page_id"],
            "before": before,
            "after": after,
            "priority_before": pri_before,
            "priority_after": pri_after,
        }
        changes.append(change)
        if pri_before != pri_after:
            violations.append(change)
    return changes, violations


def write_topics(page_id: str, topics: list[str]) -> None:
    from app import notion_writer

    notion_writer._client().pages.update(
        page_id=page_id,
        properties={"Topics": {"multi_select": [{"name": t} for t in topics]}},
    )


def apply_to_notion(
    changes: list[dict],
    dry_run: bool,
    write_fn: Callable[[str, list], None] = write_topics,
    print_fn: Callable[[str], None] = print,
) -> dict:
    written = errors = 0
    for i, change in enumerate(changes):
        label = f"[{i + 1}/{len(changes)}] {change['shortcode']}: {change['before']} -> {change['after']}"
        if dry_run:
            print_fn(f"[dry-run] {label}")
            continue
        try:
            write_fn(change["page_id"], change["after"])
            written += 1
            print_fn(label)
        except Exception as exc:  # noqa: BLE001 - one bad row must not sink the batch
            errors += 1
            print_fn(f"{label} -> ERROR: {exc}")
    return {"written": written, "errors": errors, "planned": len(changes)}


# --- Obsidian orphan cleanup ------------------------------------------------------


def note_user_content(path: Path) -> str:
    """Text the USER wrote in a topic note, ignoring the generated scaffold.

    A topic stub is `# <name>` / `## Notes` (default_header) + an AUTO block.
    Anything else — real prose under ## Notes, or a suffix after the AUTO block —
    is user content that must survive. Returns that content ('' if none)."""
    from app.obsidian_sync import AUTO_END, AUTO_START

    content = path.read_text(encoding="utf-8")
    start, end = content.find(AUTO_START), content.find(AUTO_END)
    if start != -1 and end != -1 and end > start:
        prefix = content[:start]
        suffix = content[end + len(AUTO_END):]
    else:
        prefix, suffix = content, ""

    leftover: list[str] = []
    for line in (prefix + "\n" + suffix).splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("# ") or stripped == "## Notes":
            continue  # the generated scaffold, not user content
        leftover.append(stripped)
    return "\n".join(leftover)


def find_orphaned_topic_notes(
    vault: Path, live_tags: set[str], merged_away: set[str],
) -> tuple[list[Path], list[Path], list[Path]]:
    """Classify topic notes into (deletable, preserved, unrelated_orphans).

    Only notes orphaned BY THIS MERGE are in play: a note whose tag was a merge
    SOURCE (now gone from every row). Of those, pure-auto notes are deletable and
    ones with user content are preserved. Notes that are orphaned for some OTHER
    reason (a tag removed in an earlier taxonomy state, never a merge source) are
    reported as unrelated_orphans and left completely alone — this step's remit
    is the merge, not a general vault sweep (that's vault_librarian's job)."""
    from app.obsidian_sync import _slugify

    live_slugs = {_slugify(t) for t in live_tags}
    merged_slugs = {_slugify(t) for t in merged_away}
    deletable: list[Path] = []
    preserved: list[Path] = []
    unrelated: list[Path] = []
    topics_dir = vault / "topics"
    if not topics_dir.exists():
        return deletable, preserved, unrelated
    for note in sorted(topics_dir.glob("*.md")):
        if note.stem in live_slugs:
            continue  # still a live topic
        if note.stem not in merged_slugs:
            unrelated.append(note)  # pre-existing orphan, not our merge's doing
            continue
        if note_user_content(note):
            preserved.append(note)
        else:
            deletable.append(note)
    return deletable, preserved, unrelated


def strip_auto_block(path: Path) -> None:
    """Leave a preserved orphan as a plain note: drop the generated block, add a
    one-line marker that it's orphaned-but-kept."""
    from app.obsidian_sync import AUTO_END, AUTO_START

    content = path.read_text(encoding="utf-8")
    start, end = content.find(AUTO_START), content.find(AUTO_END)
    if start != -1 and end != -1 and end > start:
        content = (content[:start].rstrip("\n") + "\n\n" + content[end + len(AUTO_END):].lstrip("\n"))
    marker = "> orphaned by a taxonomy merge — kept because it has your notes.\n\n"
    path.write_text(marker + content.lstrip("\n"), encoding="utf-8")


def run(dry_run: bool = True, print_fn: Callable[[str], None] = print) -> dict:
    from app import notion_writer, obsidian_sync

    pages = notion_writer.find_saves_pages_since("1970-01-01T00:00:00")
    rows = build_rows(pages)
    print_fn(f"Loaded {len(rows)} Saves rows.")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snap_path = Path(f"{SNAPSHOT_PREFIX}{stamp}.json")
    snapshot(rows, snap_path)
    print_fn(f"Snapshot written: {snap_path}")

    changes, violations = plan_merges(rows)
    print_fn(f"{len(changes)} rows would change tags.")

    if violations:
        print_fn(f"STOP: {len(violations)} row(s) would have a PRIORITY CHANGE — "
                 "this contradicts TAXONOMY_PROPOSAL.md §3. Not touching Notion.")
        for v in violations:
            print_fn(f"  {v['shortcode']}: {v['priority_before']} -> {v['priority_after']}  "
                     f"({v['before']} -> {v['after']})")
        return {"stopped": True, "reason": "priority_drift", "violations": violations,
                "snapshot": str(snap_path), "changes": len(changes)}

    print_fn("Priority-invariance gate PASSED (zero rows drift).")
    notion_result = apply_to_notion(changes, dry_run=dry_run, print_fn=print_fn)

    # Post-merge live tag set (what Obsidian will regenerate from).
    live_tags: set[str] = set()
    for row in rows:
        live_tags.update(apply_merges(row["topics"]))

    vault = Path(obsidian_sync.VAULT_PATH)
    merged_away = set(MERGES)
    deletable, preserved, unrelated = find_orphaned_topic_notes(vault, live_tags, merged_away)
    print_fn(f"Orphaned by THIS merge: {len(deletable)} pure-auto (delete), "
             f"{len(preserved)} with user content (preserve).")
    if unrelated:
        print_fn(f"Left untouched — {len(unrelated)} pre-existing orphan note(s) not "
                 "caused by this merge (vault_librarian's remit, not ours): "
                 + ", ".join(n.name for n in unrelated))
    deleted = 0
    for note in deletable:
        print_fn(f"  {'[dry-run] would delete' if dry_run else 'delete'}: {note.name}")
        if not dry_run:
            note.unlink()
            deleted += 1
    for note in preserved:
        print_fn(f"  {'[dry-run] would preserve' if dry_run else 'preserve'} (strip auto): {note.name}")
        if not dry_run:
            strip_auto_block(note)

    sync_result = {}
    if not dry_run:
        sync_result = obsidian_sync.sync()
        print_fn(f"Re-sync: {sync_result}")

    return {
        "stopped": False,
        "snapshot": str(snap_path),
        "notion": notion_result,
        "orphans_deleted": deleted if not dry_run else len(deletable),
        "orphans_preserved": len(preserved),
        "sync": sync_result,
        "row_count": len(rows),
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="actually edit Notion and the vault (default is a dry run)")
    args = parser.parse_args()

    result = run(dry_run=not args.apply)
    print("\n" + "=" * 70)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
