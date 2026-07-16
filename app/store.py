"""SQLite layer: saves, tags, embeddings, daily fetch counter. DATA_SCHEMA.md §4."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager

# Some Linux Python builds compile the stdlib sqlite3 WITHOUT loadable-extension
# support, so Connection has no .enable_load_extension and sqlite-vec can't load
# (this is exactly the Render failure: "'sqlite3.Connection' object has no
# attribute 'enable_load_extension'"). pysqlite3-binary bundles a SQLite built
# WITH that support; swap it in as a drop-in replacement ONLY when the stdlib
# module lacks the capability. Local Windows dev already has it, so this is a
# no-op there and behavior is unchanged. If pysqlite3 isn't installed either,
# we keep the stdlib module and the graceful-degrade path below handles it.
if not hasattr(sqlite3.Connection, "enable_load_extension"):
    try:
        import pysqlite3.dbapi2 as sqlite3  # type: ignore[no-redef]
    except ImportError:
        pass
from datetime import date, datetime, timedelta, timezone
from typing import Iterator, Optional

logger = logging.getLogger("reelbrain.store")

DB_PATH = os.environ.get("DB_PATH", "./data/reelbrain.db").strip()
EMBEDDING_DIM = 768  # Gemini embedding free tier, per DATA_SCHEMA.md §4

SCHEMA = """
CREATE TABLE IF NOT EXISTS saves (
    shortcode TEXT PRIMARY KEY,
    permalink TEXT NOT NULL,
    creator TEXT,
    creator_fullname TEXT,
    caption TEXT,
    note TEXT,
    taken_at TEXT,
    transcript TEXT,
    extraction_json TEXT,
    notion_page_id TEXT,
    notion_page_url TEXT,
    status TEXT NOT NULL,
    gate_keyword TEXT,
    gate_resource_url TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS tags (
    tag TEXT NOT NULL,
    shortcode TEXT NOT NULL,
    PRIMARY KEY (tag, shortcode)
);

CREATE TABLE IF NOT EXISTS fetch_log (
    day TEXT PRIMARY KEY,
    count INTEGER NOT NULL DEFAULT 0,
    last_fetch_at TEXT
);
"""


def _utc_naive_now() -> datetime:
    # UTC wall-clock as a NAIVE datetime. .replace(tzinfo=None) is deliberate:
    # an aware datetime's isoformat() appends "+00:00", which would change the
    # string format relative to every timestamp already in the DB (all naive
    # "YYYY-MM-DDTHH:MM:SS.ffffff") and skew lexicographic "< cutoff" comparisons.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _now_iso() -> str:
    # A single consistent format for every timestamp this module writes.
    # SQLite's own datetime('now') uses a space separator ("YYYY-MM-DD HH:MM:SS"),
    # which sorts *before* this T-separated format for the same instant — mixing
    # the two breaks every "< cutoff" comparison used by the nightly job.
    return _utc_naive_now().isoformat()


# sqlite-vec is loaded per-connection (SQLite extensions aren't persisted in the
# db file). BUILD_SPEC 3.1: "on quota error just skip — embeddings are enhancement,
# not critical path" — extended here to cover "extension unavailable" too, since a
# hosting environment that disables loadable extensions must not take the rest of
# the app down with it. Sticky flag so we don't retry a load we already know fails.
_VEC_LOAD_FAILED = False


def _load_vec_extension(conn: sqlite3.Connection) -> None:
    global _VEC_LOAD_FAILED
    if _VEC_LOAD_FAILED:
        return
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except Exception:
        _VEC_LOAD_FAILED = True
        logger.warning(
            "sqlite-vec unavailable — embeddings/near-dup/related-saves disabled", exc_info=True
        )


def vec_available() -> bool:
    return not _VEC_LOAD_FAILED


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _load_vec_extension(conn)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(SCHEMA)
        if _VEC_LOAD_FAILED:
            return
        try:
            conn.execute(
                f"""CREATE VIRTUAL TABLE IF NOT EXISTS save_vec USING vec0(
                        shortcode TEXT PRIMARY KEY,
                        embedding FLOAT[{EMBEDDING_DIM}] distance_metric=cosine
                    )"""
            )
        except sqlite3.OperationalError:
            logger.warning("could not create save_vec table — embeddings disabled", exc_info=True)


def get_by_shortcode(shortcode: str) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM saves WHERE shortcode = ?", (shortcode,)
        ).fetchone()


def insert_processing(
    shortcode: str, permalink: str, note: Optional[str] = None
) -> None:
    now = _now_iso()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO saves (shortcode, permalink, note, status, created_at, updated_at)
               VALUES (?, ?, ?, 'processing', ?, ?)""",
            (shortcode, permalink, note, now, now),
        )


def update_save(shortcode: str, **fields) -> None:
    if not fields:
        return
    fields["updated_at"] = _now_iso()
    columns = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [shortcode]
    with get_connection() as conn:
        conn.execute(f"UPDATE saves SET {columns} WHERE shortcode = ?", values)


def set_tags(shortcode: str, tags: list[str]) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM tags WHERE shortcode = ?", (shortcode,))
        conn.executemany(
            "INSERT OR IGNORE INTO tags (tag, shortcode) VALUES (?, ?)",
            [(tag, shortcode) for tag in tags],
        )


def get_taxonomy(limit: int = 40) -> list[str]:
    """Top tag candidates for the extraction prompt, per DATA_SCHEMA.md §4."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT tag, COUNT(*) c FROM tags GROUP BY tag ORDER BY c DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [row["tag"] for row in rows]


def get_failed(shortcode: str) -> Optional[sqlite3.Row]:
    return get_by_shortcode(shortcode)


# --- embeddings / near-dup / related saves (BUILD_SPEC.md §3.1) ---


def upsert_embedding(shortcode: str, vector: list[float]) -> None:
    if _VEC_LOAD_FAILED:
        return
    with get_connection() as conn:
        conn.execute("DELETE FROM save_vec WHERE shortcode = ?", (shortcode,))
        conn.execute(
            "INSERT INTO save_vec (shortcode, embedding) VALUES (?, ?)",
            (shortcode, json.dumps(vector)),
        )


def find_neighbors(vector: list[float], k: int = 4) -> list[tuple[str, float]]:
    """Nearest neighbors by cosine similarity, most similar first.

    Call this *before* upsert_embedding-ing the current row's own vector, or it'll
    show up as its own (trivially perfect) nearest neighbor.
    """
    if _VEC_LOAD_FAILED:
        return []
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT shortcode, distance FROM save_vec
               WHERE embedding MATCH ? AND k = ? ORDER BY distance""",
            (json.dumps(vector), k),
        ).fetchall()
    # sqlite-vec's cosine "distance" is 1 - cosine_similarity.
    return [(row["shortcode"], 1 - row["distance"]) for row in rows]


def count_saves_by_creator(creator_username: str) -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) c FROM saves WHERE creator = ?", (creator_username,)
        ).fetchone()
    return row["c"] if row else 0


def get_stuck_processing(older_than_minutes: int = 60) -> list[sqlite3.Row]:
    cutoff = (_utc_naive_now() - timedelta(minutes=older_than_minutes)).isoformat()
    with get_connection() as conn:
        # COALESCE(updated_at, created_at): a /retry resets status to 'processing'
        # and bumps updated_at, so a just-retried row isn't immediately re-flagged
        # as stuck just because its original created_at is old.
        return conn.execute(
            """SELECT * FROM saves WHERE status = 'processing'
               AND COALESCE(updated_at, created_at) < ?""",
            (cutoff,),
        ).fetchall()


def get_expired_gates(older_than_days: int = 7) -> list[sqlite3.Row]:
    cutoff = (_utc_naive_now() - timedelta(days=older_than_days)).isoformat()
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM saves WHERE status = 'awaiting_dm' AND updated_at < ?",
            (cutoff,),
        ).fetchall()


def get_most_recent_awaiting_dm() -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """SELECT * FROM saves WHERE status = 'awaiting_dm'
               ORDER BY updated_at DESC LIMIT 1"""
        ).fetchone()


def find_pending_gate(shortcode_or_note: Optional[str]) -> Optional[sqlite3.Row]:
    """BUILD_SPEC 2.2: match /attach's target row.

    Tries an exact shortcode match first, then a substring match against the
    note the entry was captured with, both restricted to awaiting_dm rows.
    Falls back to the most-recent awaiting_dm entry if shortcode_or_note is
    omitted or matches nothing.
    """
    if shortcode_or_note:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM saves WHERE shortcode = ? AND status = 'awaiting_dm'",
                (shortcode_or_note,),
            ).fetchone()
            if row:
                return row
            row = conn.execute(
                """SELECT * FROM saves WHERE status = 'awaiting_dm' AND note LIKE ?
                   ORDER BY updated_at DESC LIMIT 1""",
                (f"%{shortcode_or_note}%",),
            ).fetchone()
            if row:
                return row
    return get_most_recent_awaiting_dm()


def get_saves_since(days: int = 7) -> list[sqlite3.Row]:
    """All saves created in the past N days, newest first (weekly digest)."""
    cutoff = (_utc_naive_now() - timedelta(days=days)).isoformat()
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM saves WHERE created_at >= ? ORDER BY created_at DESC",
            (cutoff,),
        ).fetchall()


def get_tags_for_shortcodes(shortcodes: list[str]) -> dict[str, list[str]]:
    if not shortcodes:
        return {}
    placeholders = ",".join("?" * len(shortcodes))
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT tag, shortcode FROM tags WHERE shortcode IN ({placeholders})",
            shortcodes,
        ).fetchall()
    result: dict[str, list[str]] = {}
    for row in rows:
        result.setdefault(row["shortcode"], []).append(row["tag"])
    return result


# --- fetch rate discipline (CLAUDE.md constraint #2 / BUILD_SPEC 1.2) ---


def get_daily_fetch_count(day: Optional[str] = None) -> int:
    day = day or date.today().isoformat()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT count FROM fetch_log WHERE day = ?", (day,)
        ).fetchone()
    return row["count"] if row else 0


def get_last_fetch_at() -> Optional[float]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT last_fetch_at FROM fetch_log ORDER BY day DESC LIMIT 1"
        ).fetchone()
    if row and row["last_fetch_at"]:
        return float(row["last_fetch_at"])
    return None


def record_fetch() -> None:
    day = date.today().isoformat()
    now = str(time.time())
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO fetch_log (day, count, last_fetch_at) VALUES (?, 1, ?)
               ON CONFLICT(day) DO UPDATE SET count = count + 1, last_fetch_at = ?""",
            (day, now, now),
        )
