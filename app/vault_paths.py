"""Canonical names for the vault's ROOT-LEVEL files.

Phase D: this vault will eventually be merged into a much larger Obsidian
vault. Generic root filenames like `_index.md` or `INSTALLED.md` would collide
with whatever the destination vault already has, so every root-level file
carries a `ReelBrain-` prefix. Subfolders (reels/, topics/, resources/,
creators/) are NOT namespaced — a folder can't collide the same way, and their
contents are already reel/topic specific.

Every module reads these constants rather than hard-coding a filename, so a
future rename is one edit here instead of a grep-and-pray across the codebase.
"""
from __future__ import annotations

# The vault's home page: links only to the parent topic notes.
INDEX_FILENAME = "ReelBrain-Index.md"
# Hand-maintained record of what's actually installed on this machine.
INSTALLED_FILENAME = "ReelBrain-Installed.md"
# The Implementation Scout's ranked output.
QUEUE_FILENAME = "ReelBrain-Queue.md"
# Auto-generated Scout input (regenerated every run; never hand-edited).
SCOUT_INPUT_FILENAME = "ReelBrain-ScoutInput.md"

# Pre-namespacing names, kept ONLY so the one-time migration
# (scripts/namespace_vault_files.py) knows what to rename and so an older
# vault still resolves. Never write these.
LEGACY_NAMES = {
    "_index.md": INDEX_FILENAME,
    "INSTALLED.md": INSTALLED_FILENAME,
    "IMPLEMENTATION_QUEUE.md": QUEUE_FILENAME,
    "scout_input.md": SCOUT_INPUT_FILENAME,
}

# Stems (no .md) of every root file that is an entry point rather than an
# orphan — used by scripts/vault_orphans.py, which must not flag them.
ROOT_ENTRY_POINT_STEMS = {name[:-3] for name in LEGACY_NAMES.values()} | {
    name[:-3] for name in LEGACY_NAMES
}
