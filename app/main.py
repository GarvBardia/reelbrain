"""FastAPI: /capture /retry /attach. BUILD_SPEC.md 1.1, 1.6, 2.2, 3.1, 3.3."""
from __future__ import annotations

import hmac
import logging
import os
import re
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app import fetcher, gemini_pipe, nightly, notion_writer, store
from app.models import AttachRequest, CaptureRequest, Extraction, NightlyRequest, ReelData

logger = logging.getLogger("reelbrain")
logging.basicConfig(level=logging.INFO)

CAPTURE_SECRET = os.environ.get("CAPTURE_SECRET", "").strip()

# BUILD_SPEC 3.1 / 3.3 thresholds
NEAR_DUP_SIMILARITY = 0.92
RELATED_SIMILARITY = 0.75
RELATED_TOP_K = 3
LOW_SIGNAL_VALUE_SCORE = 2
CORE_SOURCE_SAVE_COUNT = 5

# How much of a failure reason to surface on the Notion row before truncating.
FAILURE_REASON_MAX_CHARS = 300

@asynccontextmanager
async def _lifespan(app: FastAPI):
    store.init_db()
    fetcher.log_cookie_source()
    yield


app = FastAPI(title="ReelBrain", lifespan=_lifespan)


@app.get("/")
def root() -> dict:
    return {"status": "ok"}


@app.get("/ping")
def ping() -> dict:
    """No-auth keep-alive target (SCHEDULING.md). Deliberately does nothing —
    its only job is to keep the Render free-tier instance from idling out."""
    return {"pong": True}


@app.get("/health")
def health() -> dict:
    """Render health check + a quick self-report: is sqlite-vec actually usable?
    (vec_available() alone only says a load hasn't *failed yet* — probing the
    save_vec table proves the extension loaded and the table exists.)

    `cookies_file` reports whether a burner cookies.txt was found — without one
    every fetch fails fast, so this makes the most common prod-only breakage
    checkable from a browser with no log access. Reports presence only, never
    the path's contents.

    `cookie_health` is "degraded" once AUTH_FAILURE_THRESHOLD consecutive
    cookie-backed fetches have failed with an auth-type error (login required,
    empty media response, etc.) — a distinct signal from cookies_file, since the
    file can exist and still hold expired cookies. Detection only, no auto-fix.
    """
    vec_ok = False
    if store.vec_available():
        try:
            with store.get_connection() as conn:
                conn.execute("SELECT COUNT(*) FROM save_vec").fetchone()
            vec_ok = True
        except Exception:  # noqa: BLE001 - health must never 500 over an optional feature
            vec_ok = False
    try:
        cookies_ok = fetcher.cookies_file_available()
    except Exception:  # noqa: BLE001
        cookies_ok = False
    try:
        cookie_health = fetcher.cookie_health_status()
    except Exception:  # noqa: BLE001
        cookie_health = "ok"
    return {
        "status": "ok",
        "sqlite_vec": vec_ok,
        "cookies_file": cookies_ok,
        "cookie_health": cookie_health,
        "db_path": store.DB_PATH,
    }


def _check_secret(secret: str) -> None:
    # hmac.compare_digest = constant-time comparison (no timing side channel).
    # Strip the incoming secret too: the stored CAPTURE_SECRET is stripped at
    # read time, so an inbound value with stray whitespace must be normalized the
    # same way or it would spuriously fail to match.
    if not CAPTURE_SECRET or not hmac.compare_digest(secret.strip(), CAPTURE_SECRET):
        raise HTTPException(status_code=401, detail="invalid secret")


# --- naive per-IP rate limit (in-memory; single process, <20 req/day scale) ---

RATE_LIMIT_MAX_PER_MINUTE = 30
RATE_LIMIT_WINDOW_SECONDS = 60
_rate_buckets: dict[str, deque[float]] = defaultdict(deque)


def _check_rate_limit(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    bucket = _rate_buckets[ip]
    while bucket and now - bucket[0] > RATE_LIMIT_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT_MAX_PER_MINUTE:
        raise HTTPException(status_code=429, detail="rate limit exceeded — try again in a minute")
    bucket.append(now)


_SHORTCODE_PATH_RE = re.compile(r"[A-Za-z0-9_-]{1,30}")


def _canonical_permalink(shortcode: str) -> str:
    return f"https://www.instagram.com/reel/{shortcode}/"


def _apply_embeddings_and_related(shortcode: str, extraction: Extraction) -> list[str]:
    """BUILD_SPEC 3.1: embed, find neighbors, tag near-duplicates, link Related.

    Returns the Notion page IDs of related saves (possibly empty). Never raises —
    embeddings are an enhancement, not the critical path, so any failure here
    (quota, network, sqlite-vec unavailable, dimension mismatch) is caught and
    logged, and the pipeline continues without embeddings for this row.
    """
    try:
        text = extraction.main_point + "\n" + "\n".join(extraction.supporting_points)
        vector = gemini_pipe.embed_text(text)

        # Look up neighbors *before* storing our own vector, or we'd always find
        # ourselves as a trivially-perfect nearest neighbor.
        neighbors = store.find_neighbors(vector, k=RELATED_TOP_K + 1)
        store.upsert_embedding(shortcode, vector)
    except Exception:
        logger.warning("embeddings/near-dup skipped for %s", shortcode, exc_info=True)
        return []

    related = [(sc, sim) for sc, sim in neighbors if sim > RELATED_SIMILARITY][:RELATED_TOP_K]
    if related and related[0][1] > NEAR_DUP_SIMILARITY and "near-duplicate" not in extraction.topic_tags:
        extraction.topic_tags.append("near-duplicate")

    related_page_ids = []
    for related_shortcode, _similarity in related:
        related_row = store.get_by_shortcode(related_shortcode)
        if related_row and related_row["notion_page_id"]:
            related_page_ids.append(related_row["notion_page_id"])
    return related_page_ids


def _maybe_flag_core_source(reel: ReelData, creator_page_id: str | None) -> None:
    """BUILD_SPEC 3.3: creator save-count >= 5 -> Core source checkbox."""
    if not reel.creator_username or not creator_page_id:
        return
    if store.count_saves_by_creator(reel.creator_username) >= CORE_SOURCE_SAVE_COUNT:
        try:
            notion_writer.set_core_source(creator_page_id, True)
        except Exception:
            logger.warning("failed to set Core source for %s", reel.creator_username, exc_info=True)


def _note_with_failure_reason(note: str | None, reason: str | None) -> str | None:
    """Append the failure reason to the user's note rather than replacing it, so a
    Failed row says WHY in Notion without needing log access."""
    if not reason:
        return note
    stamped = f"⚠️ {reason[:FAILURE_REASON_MAX_CHARS]}"
    return f"{note}\n\n{stamped}" if note else stamped


def run_pipeline(
    shortcode: str,
    permalink: str,
    note: str | None,
    existing_page_id: str | None = None,
) -> None:
    """The full fetch -> extract -> write pipeline. Never raises — always leaves
    the row in a terminal status and (fail-soft) writes/updates a Notion page."""
    extraction: Extraction | None = None
    related_page_ids: list[str] = []
    failure_reason: str | None = None
    status: str

    try:
        reel = fetcher.fetch_reel(shortcode, permalink)
    except fetcher.FetchDegraded as degraded:
        logger.warning("fetch degraded for %s: %s", shortcode, degraded)
        reel = degraded.partial
        status = "failed"
        failure_reason = str(degraded)
    else:
        store.update_save(
            shortcode,
            creator=reel.creator_username,
            creator_fullname=reel.creator_fullname,
            caption=reel.caption,
            taken_at=reel.taken_at,
        )
        taxonomy = store.get_taxonomy()
        extraction = gemini_pipe.run_extraction(reel, note, taxonomy)
        related_page_ids = _apply_embeddings_and_related(shortcode, extraction)
        if extraction.topic_tags:
            store.set_tags(shortcode, extraction.topic_tags)
        store.update_save(
            shortcode,
            transcript=extraction.transcript,
            extraction_json=extraction.model_dump_json(),
        )
        # Comment-gate takes priority over the low-signal filter: even a low-value
        # reel still needs the user to act on (or knowingly skip) the gate.
        if extraction.comment_gate.detected:
            status = "awaiting_dm"
        elif extraction.value_score <= LOW_SIGNAL_VALUE_SCORE:
            status = "low_signal"
        else:
            status = "done"

    notion_note = _note_with_failure_reason(note, failure_reason)
    try:
        if existing_page_id:
            result = notion_writer.update_page(
                existing_page_id, reel, extraction, status,
                note=notion_note, related_page_ids=related_page_ids,
            )
        else:
            result = notion_writer.create_page(
                reel, extraction, status, note=notion_note, related_page_ids=related_page_ids
            )
        store.update_save(
            shortcode,
            notion_page_id=result["page_id"],
            notion_page_url=result["url"],
            status=status,
            gate_keyword=extraction.comment_gate.keyword if extraction else None,
        )
        _maybe_flag_core_source(reel, result.get("creator_page_id"))
    except Exception:  # noqa: BLE001 - constraint #3: never silently drop a capture
        logger.exception("Notion write failed for %s — row left in SQLite as failed", shortcode)
        store.update_save(shortcode, status="failed")


@app.post("/capture", status_code=202)
def capture(req: CaptureRequest, request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    _check_rate_limit(request)
    _check_secret(req.secret)

    try:
        shortcode = fetcher.normalize_url(req.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # BUILD_SPEC 1.1: dedupe on shortcode existing in SQLite at all — do not
    # re-process, even if that row is a failed/degraded one. Re-running a failed
    # capture is what POST /retry is for, not re-pasting the same URL.
    #
    # BUILD_SPEC 2.2 (comment-gate assist): /capture responds before the gate is
    # even known (fetch+extraction happens in the background), so there's no way
    # to surface the keyword on the *first* share. Re-sharing the same link once
    # the pipeline has finished hits this dedupe path — that's where the keyword
    # and permalink come back, for the Shortcut to act on.
    existing = store.get_by_shortcode(shortcode)
    if existing:
        return JSONResponse(
            status_code=200,
            content={
                "status": "duplicate",
                "url": existing["notion_page_url"],
                "capture_status": existing["status"],
                "gate_keyword": existing["gate_keyword"],
                "permalink": existing["permalink"],
            },
        )

    permalink = _canonical_permalink(shortcode)
    store.insert_processing(shortcode, permalink, note=req.note)
    background_tasks.add_task(run_pipeline, shortcode, permalink, req.note)
    return JSONResponse(status_code=202, content={"status": "processing", "shortcode": shortcode})


@app.post("/retry/{shortcode}", status_code=202)
def retry(shortcode: str, request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    _check_rate_limit(request)
    if not _SHORTCODE_PATH_RE.fullmatch(shortcode):
        raise HTTPException(status_code=400, detail="malformed shortcode")
    row = store.get_by_shortcode(shortcode)
    if not row:
        raise HTTPException(status_code=404, detail="unknown shortcode")

    store.update_save(shortcode, status="processing")
    background_tasks.add_task(
        run_pipeline, shortcode, row["permalink"], row["note"], row["notion_page_id"]
    )
    return JSONResponse(status_code=202, content={"status": "processing", "shortcode": shortcode})


@app.post("/attach")
def attach(req: AttachRequest, request: Request) -> JSONResponse:
    """BUILD_SPEC 2.2: user shares the DM'd link back. Attaches it to the pending
    entry's Resources and flips Awaiting DM -> Inbox. Matches by shortcode or note
    substring; falls back to the most-recent Awaiting DM entry if omitted."""
    _check_rate_limit(request)
    _check_secret(req.secret)

    row = store.find_pending_gate(req.shortcode_or_note)
    if not row:
        raise HTTPException(status_code=404, detail="no matching awaiting-DM entry")

    store.update_save(row["shortcode"], status="done", gate_resource_url=req.resource_url)

    if row["notion_page_id"]:
        try:
            notion_writer.set_status_and_gate_resource(row["notion_page_id"], "done", req.resource_url)
        except Exception:  # noqa: BLE001 - SQLite is already updated; don't lose the attach over a Notion hiccup
            logger.exception("Notion update failed for attach on %s", row["shortcode"])

    return JSONResponse(
        status_code=200,
        content={
            "status": "attached",
            "shortcode": row["shortcode"],
            "notion_url": row["notion_page_url"],
        },
    )


@app.post("/nightly")
def nightly_endpoint(req: NightlyRequest, request: Request) -> JSONResponse:
    """HTTP trigger for the nightly cleanup job (BUILD_SPEC 2.3) — Render's free
    tier has no built-in cron, so an external scheduler (GitHub Actions, see
    SCHEDULING.md) POSTs here instead. Same code path as scripts/run_nightly.py."""
    _check_rate_limit(request)
    _check_secret(req.secret)
    result = nightly.run()
    return JSONResponse(status_code=200, content=result)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
