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


def row_title(row: sqlite3.Row) -> str:
    """Locally-derived equivalent of the Notion page's Title (= the extraction's
    main_point) — there's no separate title column in SQLite. Rows with a real
    gate always have extraction_json populated, since gate detection happens
    after extraction in run_pipeline, so this is never empty in practice for
    them."""
    if not row["extraction_json"]:
        return ""
    try:
        return json.loads(row["extraction_json"]).get("main_point") or ""
    except (json.JSONDecodeError, AttributeError):
        return ""


def resolve_exact_shortcode(shortcode: str) -> tuple[bool, Optional[sqlite3.Row]]:
    """/attach's ONLY auto-commit resolution path (see PROGRESS.md — the
    substring/"sole Awaiting DM row" fallback tiers were REMOVED entirely
    after a real cross-attachment: a resource meant for one reel landed on a
    different, coincidentally-similar-sounding one, reported as a genuine
    "success" with no ambiguity ever detected because there was only one
    candidate. Anything short of an exact shortcode now goes through
    app/attach_matching.py's candidate-scoring path instead, which returns
    ranked candidates for a human to confirm — it never auto-commits.

    Returns (exists, row):
      (False, None) -> this shortcode doesn't exist anywhere (local, any
          status, or Notion) — the caller should treat this as "no exact
          match" and fall through to candidate scoring.
      (True, None)  -> a row/page for this EXACT shortcode was found, but it
          isn't awaiting_dm right now — the caller must return this as a
          clean "not found" and must NOT fall through to guessing among
          other rows.
      (True, row)   -> the exact row, and it IS awaiting_dm.

    THE ORIGINAL BUG THIS FIXES (BUG 3, kept from the prior design): checking
    only the caller's already-status-filtered awaiting_dm list meant a
    requested row absent from that list (e.g. after an ephemeral-disk wipe)
    silently fell through to an unrelated row. Checking the full local table
    (any status) first, then Notion directly by shortcode, closes that gap:
    this shortcode is searched for everywhere before any other-row logic is
    even considered.
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
        # falling through to guess among unrelated rows.
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


def _is_attach_candidate(status: Optional[str], gate_keyword: Optional[str], gate_resource_url: Optional[str]) -> bool:
    """A row can still legitimately accept a DM'd resource if it's Awaiting
    DM, OR it has a Gate keyword but no Gate resource yet (the BUG2 edge case
    where a keyword got set without `detected` flipping true, routing the row
    to Inbox instead of Awaiting DM). Shared by the candidate-scoring pool and
    /attach/confirm's own target validation, so both use the exact same
    definition of "still open"."""
    return status == "awaiting_dm" or bool(gate_keyword and not gate_resource_url)


def resolve_attachable_by_shortcode(shortcode: str) -> Optional[sqlite3.Row]:
    """Used by /attach/confirm: accepts the row only if it's still a genuine
    open attach target per _is_attach_candidate — broader than
    resolve_exact_shortcode's awaiting_dm-only check (matching the same
    broadened pool get_attach_candidates() scores against), but still an
    EXACT shortcode lookup, never a substitution."""
    local_row = get_by_shortcode(shortcode)
    if local_row is not None:
        if _is_attach_candidate(local_row["status"], local_row["gate_keyword"], local_row["gate_resource_url"]):
            return local_row
        return None

    from app import notion_writer

    try:
        page = notion_writer.find_page_by_shortcode(shortcode)
    except Exception:
        logger.warning(
            "Notion exact-shortcode lookup failed for %s during /attach/confirm",
            shortcode, exc_info=True,
        )
        return None
    if page is None:
        return None

    fields = notion_writer.extract_saves_fields(page)
    status = notion_writer.status_label_from_notion(fields["status_label"])
    gate_resource_url = (page.get("properties", {}).get("Gate resource") or {}).get("url")
    if _is_attach_candidate(status, fields["gate_keyword"], gate_resource_url):
        return _persist_notion_page(page)
    return None


def _local_attach_candidates() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM saves WHERE status = 'awaiting_dm'
               OR (gate_keyword IS NOT NULL AND gate_keyword != ''
                   AND (gate_resource_url IS NULL OR gate_resource_url = ''))"""
        ).fetchall()
    tags_by_shortcode = get_tags_for_shortcodes([r["shortcode"] for r in rows])
    return [
        {
            "shortcode": row["shortcode"],
            "title": row_title(row),
            "note": row["note"] or "",
            "gate_keyword": row["gate_keyword"] or "",
            "topics": tags_by_shortcode.get(row["shortcode"], []),
            "created_at": row["created_at"] or "",
            "page_id": row["notion_page_id"],
        }
        for row in rows
    ]


def get_attach_candidates() -> list[dict]:
    """Notion-primary (durable) candidate pool for /attach's scoring
    resolution path: Awaiting DM rows, plus Inbox rows that have a Gate
    keyword but no Gate resource yet. Falls back to local SQLite only when
    the Notion query fails outright — never silently empty due to a
    transient hiccup (same FIX 2 pattern the digests use)."""
    from app import notion_writer

    try:
        pages = notion_writer.find_attach_candidate_pages()
    except Exception:
        logger.warning(
            "Notion attach-candidate query failed — falling back to local SQLite", exc_info=True,
        )
        return _local_attach_candidates()

    candidates = []
    for page in pages:
        fields = notion_writer.extract_saves_fields(page)
        if not fields["shortcode"]:
            continue
        digest_fields = notion_writer.extract_digest_fields(page)
        candidates.append({
            "shortcode": fields["shortcode"],
            "title": fields["title"],
            "note": fields["note"] or "",
            "gate_keyword": fields["gate_keyword"] or "",
            "topics": digest_fields["topics"],
            # Phase G: the exact tools this reel named. This is what makes
            # attach_matching's NAMED_ENTITY_MATCH_WEIGHT actually do anything
            # -- before it was persisted, that whole code path was inert.
            "named_entities": digest_fields.get("named_entities") or [],
            "created_at": page.get("created_time", ""),
            "page_id": fields["page_id"],
        })
    return candidates


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


def get_saves_since_hours(hours: int = 24) -> list[sqlite3.Row]:
    """All saves created in the past N hours, newest first (daily digest).
    A separate function from get_saves_since (rather than get_saves_since(days=1))
    so the 24-hour window is exact and independently testable, not an
    assumption riding on days-to-hours equivalence."""
    cutoff = (_utc_naive_now() - timedelta(hours=hours)).isoformat()
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
