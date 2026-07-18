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

-- Generic small-state key/value store (cookie-health counter, alert dedup dates,
-- anything else that's a single persistent value rather than a row per record).
CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT
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


def get_embedding(shortcode: str) -> Optional[list[float]]:
    """Read a stored embedding back out (Obsidian sync reuses capture-time vectors
    rather than recomputing). sqlite-vec stores FLOAT[N] as a raw float32 blob."""
    if _VEC_LOAD_FAILED:
        return None
    import struct

    with get_connection() as conn:
        row = conn.execute(
            "SELECT embedding FROM save_vec WHERE shortcode = ?", (shortcode,)
        ).fetchone()
    if row is None:
        return None
    blob = row["embedding"]
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


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


# --- ephemeral-disk recovery: Notion is the durable source of truth --------
#
# Render's free tier wipes its ephemeral disk on every redeploy/restart, taking
# SQLite with it. When a local lookup misses entirely, /attach and /retry fall
# back to querying Notion directly (the durable copy) and persist whatever's
# found locally as a byproduct, so the rest of this process session — and any
# later lookups before the next wipe — see a normal local row.


def upsert_from_notion(
    *,
    shortcode: str,
    permalink: str,
    note: Optional[str],
    status: str,
    notion_page_id: str,
    notion_page_url: str,
    gate_keyword: Optional[str] = None,
) -> sqlite3.Row:
    """Persists a minimal row reconstructed from a Notion page and returns it as
    a genuine local row. ON CONFLICT covers the (unlikely) case where a row
    already exists locally under a different status — e.g. a race with a
    background pipeline — without clobbering a note the row already had."""
    now = _now_iso()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO saves
                   (shortcode, permalink, note, status, notion_page_id, notion_page_url,
                    gate_keyword, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(shortcode) DO UPDATE SET
                   permalink = excluded.permalink,
                   note = COALESCE(saves.note, excluded.note),
                   status = excluded.status,
                   notion_page_id = excluded.notion_page_id,
                   notion_page_url = excluded.notion_page_url,
                   gate_keyword = excluded.gate_keyword,
                   updated_at = excluded.updated_at""",
            (shortcode, permalink, note, status, notion_page_id, notion_page_url,
             gate_keyword, now, now),
        )
    return get_by_shortcode(shortcode)


def _persist_notion_page(page: dict) -> Optional[sqlite3.Row]:
    from app import notion_writer

    fields = notion_writer.extract_saves_fields(page)
    if not fields["shortcode"]:
        return None
    return upsert_from_notion(
        shortcode=fields["shortcode"],
        permalink=fields["permalink"] or f"https://www.instagram.com/reel/{fields['shortcode']}/",
        note=fields["note"],
        # Whichever status we reconstruct here is provisional — both /attach and
        # /retry immediately overwrite it (to 'done' / 'processing' respectively)
        # right after finding the row, so an unmapped label defaulting to 'done'
        # is harmless.
        status=notion_writer.status_label_from_notion(fields["status_label"]) or "done",
        notion_page_id=fields["page_id"],
        notion_page_url=fields["url"],
        gate_keyword=fields["gate_keyword"],
    )


def get_by_shortcode_or_notion(shortcode: str) -> Optional[sqlite3.Row]:
    """get_by_shortcode, falling back to a direct Notion lookup (and local
    re-insert) if the local row is missing. Used by /retry."""
    row = get_by_shortcode(shortcode)
    if row:
        return row
    from app import notion_writer

    try:
        page = notion_writer.find_page_by_shortcode(shortcode)
    except Exception:
        logger.warning("Notion fallback lookup failed for %s", shortcode, exc_info=True)
        return None
    if not page:
        return None
    return _persist_notion_page(page)


class AmbiguousGateMatch(Exception):
    """Raised by find_pending_gate when a substring/fallback match isn't unique.

    Real incident: with several rows simultaneously Awaiting DM, a loose
    note/title substring match attached a DM'd resource to the WRONG pending
    entry. From here on, this lookup never guesses when more than one
    candidate matches — it raises this exception (main.py translates it to
    HTTP 409) listing every matching shortcode, so the caller retries with an
    explicit one instead of silently getting whichever matched first.
    """

    def __init__(self, candidates: list[str]):
        self.candidates = candidates
        super().__init__(f"{len(candidates)} ambiguous awaiting-DM candidates: {candidates}")


def _awaiting_dm_rows() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM saves WHERE status = 'awaiting_dm' ORDER BY updated_at DESC"
        ).fetchall()


def _row_title(row: sqlite3.Row) -> str:
    """Locally-derived equivalent of the Notion page's Title (= the extraction's
    main_point) — there's no separate title column in SQLite. awaiting_dm rows
    always have extraction_json populated, since gate detection happens after
    extraction in run_pipeline, so this is never empty in practice for them."""
    if not row["extraction_json"]:
        return ""
    try:
        return json.loads(row["extraction_json"]).get("main_point") or ""
    except (json.JSONDecodeError, AttributeError):
        return ""


def _find_pending_gate_via_notion(shortcode_or_note: Optional[str]) -> Optional[sqlite3.Row]:
    """Notion-side substring/fallback search. By the time find_pending_gate calls
    this, it has already exhaustively checked for an exact shortcode match (both
    locally, across ALL statuses, and directly against Notion) and found nothing
    anywhere — so shortcode_or_note here, if not None, is only ever a note/title
    search fragment, never a candidate exact shortcode. No exact-match branch is
    needed (or safe to re-add): re-scoping it to this function's own
    find_awaiting_dm_pages() listing is exactly the bug that made BUG 3 possible
    in the sibling local path (see find_pending_gate / _resolve_exact_shortcode)."""
    from app import notion_writer

    try:
        pages = notion_writer.find_awaiting_dm_pages()
    except Exception:
        logger.warning("Notion fallback lookup for awaiting-DM entries failed", exc_info=True)
        return None
    if not pages:
        return None

    entries = [(page, notion_writer.extract_saves_fields(page)) for page in pages]

    if shortcode_or_note:
        needle = shortcode_or_note.lower()
        matches = [
            (p, f) for p, f in entries
            if needle in (f["note"] or "").lower() or needle in (f["title"] or "").lower()
        ]
        if len(matches) > 1:
            raise AmbiguousGateMatch([f["shortcode"] for _p, f in matches])
        if len(matches) == 1:
            return _persist_notion_page(matches[0][0])

    if len(pages) > 1:
        raise AmbiguousGateMatch([f["shortcode"] for _p, f in entries])
    return _persist_notion_page(pages[0])


def _resolve_exact_shortcode(shortcode: str) -> tuple[bool, Optional[sqlite3.Row]]:
    """The critical safety property (post BUG-3 incident): an explicit, exact
    shortcode must resolve to THAT row, or to nothing at all — NEVER a
    different row.

    Returns (exists, row):
      (False, None) -> this shortcode doesn't exist anywhere (local, any
          status, or Notion) -- the caller should fall through and treat
          shortcode_or_note as a note/title search fragment instead.
      (True, None)  -> a row/page for this EXACT shortcode was found, but it
          isn't awaiting_dm right now -- the caller must return this as-is
          (None) and must NOT fall through to guessing among other rows.
      (True, row)   -> the exact row, and it IS awaiting_dm.

    THE BUG THIS FIXES: the previous version only ever looked for an exact
    shortcode match within the caller's already-status-filtered awaiting_dm
    list. If the requested row wasn't in that list for any reason — most
    plausibly, Render's ephemeral disk had wiped local SQLite and this
    specific shortcode hadn't been re-synced from Notion yet — the exact-match
    check silently found nothing and execution fell through to the
    single-remaining-row auto-pick meant only for an OMITTED shortcode_or_note,
    attaching the resource to a totally unrelated pending row. Checking the
    full local table (any status) first, then Notion directly by shortcode
    (also status-unscoped, via the same primitive /retry already uses) closes
    that gap: this shortcode is now searched for everywhere BEFORE any
    other-row fallback is even considered.
    """
    local_row = get_by_shortcode(shortcode)
    if local_row is not None:
        return True, (local_row if local_row["status"] == "awaiting_dm" else None)

    from app import notion_writer

    try:
        page = notion_writer.find_page_by_shortcode(shortcode)
    except Exception:
        # Fail CLOSED, not open: we couldn't verify one way or the other, so the
        # safe answer is "this shortcode is spoken for" (refuse), never silently
        # falling through to guess among unrelated awaiting_dm rows.
        logger.warning(
            "Notion exact-shortcode lookup failed for %s — refusing to fall "
            "back to guessing a different row", shortcode, exc_info=True,
        )
        return True, None
    if page is None:
        return False, None

    fields = notion_writer.extract_saves_fields(page)
    if notion_writer.status_label_from_notion(fields["status_label"]) != "awaiting_dm":
        return True, None
    return True, _persist_notion_page(page)


def find_pending_gate(shortcode_or_note: Optional[str]) -> Optional[sqlite3.Row]:
    """BUILD_SPEC 2.2: match /attach's target row.

    SAFETY (post-incident #1, see AmbiguousGateMatch): never guesses when more
    than one candidate matches — raises AmbiguousGateMatch instead.

    SAFETY (post-incident #2, CRITICAL, see _resolve_exact_shortcode): an
    explicit shortcode_or_note that IS a real shortcode resolves to THAT row,
    or to nothing — NEVER a different row, regardless of what else happens to
    be awaiting_dm at the time.

    Priority:
      1. Exact shortcode match — checked across the FULL local saves table
         (any status), then directly against Notion if absent locally. Always
         unambiguous (shortcode is a primary key) and never substituted.
      2. Substring match against note OR title, restricted to awaiting_dm rows.
         2+ matches -> AmbiguousGateMatch.
      3. If shortcode_or_note was omitted, or matched nothing above: the single
         awaiting_dm row, if there is exactly one. 2+ rows -> AmbiguousGateMatch.
         Falls back to Notion (ephemeral-disk recovery) only when local has NONE.
    """
    if shortcode_or_note:
        exists, row = _resolve_exact_shortcode(shortcode_or_note)
        if exists:
            return row  # the correct row, or None — never a substitute

    local_rows = _awaiting_dm_rows()

    if shortcode_or_note:
        needle = shortcode_or_note.lower()
        matches = [
            r for r in local_rows
            if needle in (r["note"] or "").lower() or needle in _row_title(r).lower()
        ]
        if len(matches) > 1:
            raise AmbiguousGateMatch([r["shortcode"] for r in matches])
        if len(matches) == 1:
            return matches[0]

    if local_rows:
        if len(local_rows) > 1:
            raise AmbiguousGateMatch([r["shortcode"] for r in local_rows])
        return local_rows[0]

    return _find_pending_gate_via_notion(shortcode_or_note)


def get_archivable(older_than_days: int = 30, max_value_score: int = 2) -> list[sqlite3.Row]:
    """Stale low-value rows for the nightly auto-archive: value_score <= 2 and no
    activity on the row for 30+ days (updated_at is our best 'untouched' proxy —
    Notion-side edits to My note aren't visible to us, so any pipeline/API touch
    counts as activity). Rows still in flight (processing) or waiting on the user
    (awaiting_dm) are never archived out from under their own workflow."""
    cutoff = (_utc_naive_now() - timedelta(days=older_than_days)).isoformat()
    with get_connection() as conn:
        return conn.execute(
            """SELECT * FROM saves
               WHERE status NOT IN ('processing', 'awaiting_dm', 'archived')
                 AND extraction_json IS NOT NULL
                 AND CAST(json_extract(extraction_json, '$.value_score') AS INTEGER) <= ?
                 AND COALESCE(updated_at, created_at) < ?""",
            (max_value_score, cutoff),
        ).fetchall()


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


# --- small persistent state (cookie-health counter, alert dedup, etc.) --------


def get_state(key: str, default: Optional[str] = None) -> Optional[str]:
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_state(key: str, value: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO app_state (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (key, value),
        )
