"""One-off helper: delete a row from the local SQLite DB by shortcode, so a
smoke test can be rerun cleanly. Does NOT touch Notion — delete the Notion
page manually first, then run this.

Usage:
    python scripts/delete_row.py DavJiHqPz95
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# Make `python scripts/delete_row.py` work as well as `python -m scripts.delete_row`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import store

if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python scripts/delete_row.py <shortcode>")
    shortcode = sys.argv[1]
    store.init_db()
    db_path = getattr(store, "DB_PATH", "data/reelbrain.db")
    conn = sqlite3.connect(db_path)
    cur = conn.execute("DELETE FROM saves WHERE shortcode = ?", (shortcode,))
    conn.commit()
    print(f"deleted {cur.rowcount} row(s) for shortcode={shortcode}")
