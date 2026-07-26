"""Task 2 (see PROGRESS.md): one-time migration renaming every existing
reels/*.md note from its old {date}-{shortcode}.md filename to a slugified
main_point filename (app.obsidian_sync.slugify_main_point — same convention
sync_to_obsidian.py now uses for brand-new notes too, see note_filename()),
and rewriting every [[reels/{old-stem}]] wikilink across the ENTIRE vault
(topics/, creators/, resources/, _index.md, other reel notes' own Related
sections) to point at the new filename.

Frontmatter `shortcode:` is untouched -- only the filename changes; Notion
operations never depend on the vault filename.

Collision handling: two reels with a near-identical main_point would
slugify to the same name -- processed in shortcode order for determinism,
the SECOND one to claim a slug gets its shortcode appended as a suffix.

Idempotent: a note whose computed slug already matches its current filename
is left untouched (0 renames on a second run).

Usage:
    python scripts/rename_reel_notes.py --dry-run   # print planned renames only
    python scripts/rename_reel_notes.py             # rename + rewrite links
"""
from __future__ import annotations

import argparse
import random
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

FRONTMATTER_SHORTCODE_RE = re.compile(r"^shortcode:\s*(\S+)\s*$", re.MULTILINE)
H1_TITLE_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_WIKILINK_REEL_RE = re.compile(r"reels/([\w.-]+)")


def parse_reel_note(path: Path) -> Optional[dict]:
    """{"shortcode", "title", "old_stem"} from a reel note's frontmatter +
    H1 heading, or None if either is missing (never guessed)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    shortcode_match = FRONTMATTER_SHORTCODE_RE.search(text)
    title_match = H1_TITLE_RE.search(text)
    if not shortcode_match or not title_match:
        return None
    return {"shortcode": shortcode_match.group(1), "title": title_match.group(1).strip(), "old_stem": path.stem}


def compute_renames(notes: list[dict]) -> dict[str, str]:
    """old_stem -> new_stem for every note whose computed slug differs from
    its current stem. Processes in shortcode order for determinism; a slug
    collision against an ALREADY-CLAIMED new stem gets the shortcode
    appended. Never produces two different old_stems mapping to the same
    new_stem (asserted)."""
    from app.obsidian_sync import slugify_main_point

    rename_map: dict[str, str] = {}
    claimed: set[str] = set()
    for note in sorted(notes, key=lambda n: n["shortcode"]):
        slug = slugify_main_point(note["title"])
        if slug in claimed:
            slug = f"{slug}-{note['shortcode'].lower()}"
        claimed.add(slug)
        if slug != note["old_stem"]:
            rename_map[note["old_stem"]] = slug

    targets = list(rename_map.values())
    assert len(targets) == len(set(targets)), "rename collision produced duplicate target stems"
    return rename_map


def rewrite_links(text: str, rename_map: dict[str, str]) -> str:
    def _sub(match: re.Match) -> str:
        old_stem = match.group(1)
        new_stem = rename_map.get(old_stem)
        return f"reels/{new_stem}" if new_stem else match.group(0)

    return _WIKILINK_REEL_RE.sub(_sub, text)


def apply_renames(vault: Path, rename_map: dict[str, str]) -> int:
    """Renames the files on disk, then rewrites every reels/{old} reference in
    every .md file in the vault (reels/topics/creators/resources/_index.md).
    Returns the number of files actually renamed."""
    count = 0
    for old_stem, new_stem in rename_map.items():
        old_path = vault / "reels" / f"{old_stem}.md"
        new_path = vault / "reels" / f"{new_stem}.md"
        if not old_path.exists():
            continue
        old_path.rename(new_path)
        count += 1

    for md_path in vault.rglob("*.md"):
        if ".obsidian" in md_path.parts:
            continue
        try:
            text = md_path.read_text(encoding="utf-8")
        except OSError:
            continue
        new_text = rewrite_links(text, rename_map)
        if new_text != text:
            md_path.write_text(new_text, encoding="utf-8")

    return count


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vault", default=None, help="vault path (default: app.obsidian_sync.VAULT_PATH)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from app.obsidian_sync import VAULT_PATH

    vault = Path(args.vault or VAULT_PATH)
    reels_dir = vault / "reels"

    notes = []
    skipped = []
    for path in sorted(reels_dir.glob("*.md")):
        parsed = parse_reel_note(path)
        if parsed is None:
            skipped.append(path.name)
            continue
        notes.append(parsed)

    print(f"scanned {len(notes) + len(skipped)} reel note(s) ({len(skipped)} unparseable, skipped)")
    if skipped:
        print(f"  skipped: {skipped}")

    rename_map = compute_renames(notes)
    print(f"\n{len(rename_map)} rename(s) planned:\n")
    for old_stem, new_stem in list(rename_map.items())[:20]:
        print(f"  {old_stem}.md\n    -> {new_stem}.md")
    if len(rename_map) > 20:
        print(f"  ... and {len(rename_map) - 20} more")

    if args.dry_run:
        print(f"\n[dry-run] {len(rename_map)} file(s) would be renamed; no changes made.")
        return

    if not rename_map:
        print("\nnothing to rename.")
        return

    count = apply_renames(vault, rename_map)
    print(f"\nrenamed {count} file(s), rewrote wikilinks across the vault.")

    sample = random.sample(list(rename_map.items()), min(3, len(rename_map)))
    print("\nspot-check (3 random before/after pairs):")
    for old_stem, new_stem in sample:
        print(f"  {old_stem}.md  ->  {new_stem}.md")


if __name__ == "__main__":
    main()
