"""One-time migration: namespace the vault's ROOT-LEVEL files (Phase D).

This vault will eventually be merged into a much larger Obsidian vault, where
generic root filenames (_index.md, INSTALLED.md, ...) would collide. This
renames them to their ReelBrain- prefixed equivalents (app/vault_paths.py holds
the mapping) and rewrites every wikilink that referenced the old names.

Subfolders (reels/, topics/, resources/, creators/) are deliberately NOT
touched — a folder can't collide the same way.

Idempotent: a vault already migrated reports 0 renames.

Usage:
    python scripts/namespace_vault_files.py --dry-run
    python scripts/namespace_vault_files.py
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app.vault_paths import LEGACY_NAMES


def plan_renames(vault: Path) -> dict[str, str]:
    """{old_filename: new_filename} for root files that still need renaming."""
    plan = {}
    for old, new in LEGACY_NAMES.items():
        if (vault / old).exists() and not (vault / new).exists():
            plan[old] = new
    return plan


def rewrite_links(text: str, plan: dict[str, str]) -> str:
    """Rewrite [[_index]] / [[_index.md]] / [[_index|alias]] to the new stem.
    Only touches links whose target is exactly a renamed root file, so a
    reel note that merely mentions the word stays untouched."""
    for old, new in plan.items():
        old_stem, new_stem = old[:-3], new[:-3]
        # [[old_stem]], [[old_stem|alias]], [[old_stem.md]], [[old_stem#heading]]
        text = re.sub(
            r"\[\[" + re.escape(old_stem) + r"(\.md)?(?=[\]|#])",
            f"[[{new_stem}",
            text,
        )
    return text


def apply(vault: Path, plan: dict[str, str], dry_run: bool = True, print_fn=print) -> tuple[int, int]:
    """(files_renamed, files_relinked)."""
    renamed = 0
    for old, new in plan.items():
        print_fn(f"  {old}  ->  {new}")
        if not dry_run:
            (vault / old).rename(vault / new)
        renamed += 1

    relinked = 0
    for md in vault.rglob("*.md"):
        if ".obsidian" in md.parts:
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        new_text = rewrite_links(text, plan)
        if new_text != text:
            relinked += 1
            if not dry_run:
                md.write_text(new_text, encoding="utf-8")
    return renamed, relinked


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vault", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from app.obsidian_sync import VAULT_PATH

    vault = Path(args.vault or VAULT_PATH)
    plan = plan_renames(vault)

    if not plan:
        print("nothing to rename — vault root is already namespaced.")
        return

    print(f"{len(plan)} root file(s) to namespace:")
    renamed, relinked = apply(vault, plan, dry_run=args.dry_run)
    if args.dry_run:
        print(f"\n[dry-run] would rename {renamed} file(s) and rewrite links in {relinked} file(s).")
    else:
        print(f"\nrenamed {renamed} file(s); rewrote links in {relinked} file(s).")


if __name__ == "__main__":
    main()
