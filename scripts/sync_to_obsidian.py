"""Sync every Notion save into the local Obsidian vault. LOCAL-ONLY — reads .env,
hits the real Notion API (read-only) and the local SQLite DB. Never deploy to Render.

Usage:
    python scripts/sync_to_obsidian.py            # uses VAULT_PATH from .env
    python scripts/sync_to_obsidian.py D:\\Vault  # or an explicit path

See VAULT.md for what the vault looks like and how to browse it in Obsidian.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make `python scripts/sync_to_obsidian.py` work as well as `python -m scripts.sync_to_obsidian`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app import obsidian_sync, store


def main() -> None:
    store.init_db()
    vault_path = sys.argv[1] if len(sys.argv) > 1 else None
    result = obsidian_sync.sync(vault_path)
    print(f"vault:         {result['vault']}")
    print(f"notes written: {result['notes_written']}")
    print(f"topics:        {result['topics']}")
    print("\nOpen the vault folder in Obsidian (Open folder as vault) and check the graph view.")


if __name__ == "__main__":
    main()
