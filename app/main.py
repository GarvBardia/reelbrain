"""FastAPI: /capture /retry /attach /attach/confirm. BUILD_SPEC.md 1.1, 1.6, 2.2, 3.1, 3.3."""
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
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import (
    attach_audit,
    attach_matching,
    digest,
    fetcher,
    gemini_pipe,
    nightly,
    notion_writer,
    resource_lookup,
    store,
)
from app.models import (
    AttachConfirmRequest,
    AttachRequest,
    CaptureRequest,
    Extraction,
    NightlyRequest,
    ReelData,
)

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


@app.exception_handler(RequestValidationError)
async def _log_validation_errors(request: Request, exc: RequestValidationError) -> JSONResponse:
    """A 422 happens BEFORE the endpoint body runs, so it never reaches
    attach_audit.record() or any other in-handler logging — it was a real
    blind spot (see PROGRESS.md: a live /attach/confirm 422 left zero trace
    of what was actually sent, forcing a guess at the request shape instead
    of a diagnosis). This logs the raw body + the exact validation errors
    server-side before returning the SAME response FastAPI's default handler
    would have given — client-visible behavior is unchanged, only visibility
    is added."""
    try:
        raw_body = await request.body()
    except Exception:  # noqa: BLE001 - logging aid only, must never mask the real 422
        raw_body = b"<unreadable>"
    logger.warning(
        "422 validation failed for %s %s — body=%r errors=%s",
        request.method, request.url.path, raw_body[:2000], exc.errors(),
    )
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(exc.errors())})


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
        if reel.is_photo_or_carousel:
            # No video ever exists for these (yt-dlp is video-only) — a lighter
            # caption-only Gemini call replaces the bare-caption placeholder the
            # earlier fix used, so these rows get a real summary instead of
            # being permanently second-class. run_extraction itself is neither
            # called nor touched here — normal video reels are unaffected.
            extraction = gemini_pipe.run_caption_only_extraction(
                reel.caption, reel.creator_username, note, taxonomy
            )
        else:
            extraction = gemini_pipe.run_extraction(reel, note, taxonomy)
        related_page_ids = _apply_embeddings_and_related(shortcode, extraction)
        if extraction.topic_tags:
            store.set_tags(shortcode, extraction.topic_tags)
        store.update_save(
            shortcode,
            transcript=extraction.transcript,
            extraction_json=extraction.model_dump_json(),
        )
        # Photo/carousel posts (yt-dlp is video-only, can never fetch these) take
        # priority over everything else: retrying can never help, so this must
        # never end up "Failed — retry" regardless of what the degraded
        # extraction's gate/value-score would otherwise imply.
        if reel.is_photo_or_carousel:
            status = "photo_manual"
        # Comment-gate takes priority over the low-signal filter: even a low-value
        # reel still needs the user to act on (or knowingly skip) the gate.
        elif extraction.comment_gate.detected:
            status = "awaiting_dm"
        elif extraction.value_score <= LOW_SIGNAL_VALUE_SCORE:
            status = "low_signal"
        else:
            status = "done"

    notion_note = _note_with_failure_reason(note, failure_reason or reel.fetch_note)
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

    # BUILD_SPEC 1.1: dedupe on shortcode existing at all — do not re-process,
    # even if that row is a failed/degraded one. Re-running a failed capture is
    # what POST /retry is for, not re-pasting the same URL.
    #
    # Checks local SQLite first, then falls back to Notion (the durable source)
    # — the same pattern /retry and /attach already use. Local-only dedupe
    # caused a REAL duplicate (DabVtQoCI2p, two identical Notion pages): a
    # redeploy wiped the local row between two shares of the same post, and the
    # second share sailed past the dedupe. Fail-open on Notion errors: if the
    # fallback lookup itself fails, capture proceeds as new rather than
    # rejecting a save over a Notion hiccup.
    #
    # BUILD_SPEC 2.2 (comment-gate assist): /capture responds before the gate is
    # even known (fetch+extraction happens in the background), so there's no way
    # to surface the keyword on the *first* share. Re-sharing the same link once
    # the pipeline has finished hits this dedupe path — that's where the keyword
    # and permalink come back, for the Shortcut to act on.
    existing = store.get_by_shortcode_or_notion(shortcode)
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
    # Falls back to querying Notion directly (the durable source of truth) if the
    # local row is missing — Render's ephemeral disk gets wiped on every
    # redeploy/restart, but the Notion page survives.
    row = store.get_by_shortcode_or_notion(shortcode)
    if not row:
        raise HTTPException(status_code=404, detail="unknown shortcode")

    store.update_save(shortcode, status="processing")
    background_tasks.add_task(
        run_pipeline, shortcode, row["permalink"], row["note"], row["notion_page_id"]
    )
    return JSONResponse(status_code=202, content={"status": "processing", "shortcode": shortcode})


def _candidate_summary(candidate: dict) -> dict:
    """The response shape for one /attach candidate — enough to actually
    differentiate it for a human, not three identically-labeled options (see
    PROGRESS.md's redesign notes): shortcode, created date, topic tags, and
    the first line of the main_point/title."""
    title = (candidate.get("title") or "").strip()
    first_line = title.splitlines()[0] if title else "(no title)"
    return {
        "shortcode": candidate["shortcode"],
        "created": (candidate.get("created_at") or "")[:10],
        "topics": candidate.get("topics") or [],
        "main_point": first_line[:140],
        "gate_keyword": candidate.get("gate_keyword") or None,
        "match_score": candidate.get("match_score"),
    }


def _commit_attach(row, resource_url: str) -> bool:
    """Requirement (see PROGRESS.md): /attach must NEVER report success unless
    a write actually landed in Notion — the durable source of truth. The
    OLD code updated local SQLite first and swallowed a Notion failure,
    reporting 200 regardless ("SQLite is already updated, don't lose the
    attach over a Notion hiccup"). On Render, SQLite is ephemeral — a
    swallowed Notion failure could look identical to success with zero
    durable trace, which is exactly the shape of the Da8IIonEhGR incident's
    OTHER possible cause. Notion write now happens FIRST and must succeed;
    only then is local SQLite updated and does the caller get to report
    success."""
    page_id = row["notion_page_id"]
    if not page_id:
        fresh = store.get_by_shortcode_or_notion(row["shortcode"])
        page_id = fresh["notion_page_id"] if fresh else None
    if not page_id:
        logger.error(
            "attach: no notion_page_id resolvable for %s — refusing to report success",
            row["shortcode"],
        )
        return False
    try:
        notion_writer.set_status_and_gate_resource(page_id, "done", resource_url)
    except Exception:  # noqa: BLE001 - must NOT report success when the durable write failed
        logger.exception("attach: Notion write failed for %s — NOT reporting success", row["shortcode"])
        return False
    store.update_save(row["shortcode"], status="done", gate_resource_url=resource_url, notion_page_id=page_id)
    return True


@app.post("/attach")
def attach(req: AttachRequest, request: Request) -> JSONResponse:
    """BUILD_SPEC 2.2 (redesigned — see PROGRESS.md): user shares the DM'd
    link back.

    ONLY an exact shortcode auto-commits. The old "substring match" and "sole
    Awaiting DM row" fallback tiers were removed entirely after a real
    cross-attachment (a resource landed on a different, coincidentally-
    similar-sounding reel with a genuine "success" — no ambiguity was ever
    detected because there was only one candidate, so the old safety net
    couldn't catch it).

    Anything short of an exact shortcode now fetches the resource_url's own
    title/description and scores it against currently-open gates (Awaiting
    DM, or Inbox-with-a-keyword-but-no-resource-yet). This NEVER auto-
    commits: it returns up to 3 ranked candidates for the caller to confirm
    via POST /attach/confirm, or a clear "unresolved" if nothing scores
    above the confidence threshold.

    Response shape (see PROGRESS.md — flattened, always HTTP 200 for these
    four expected business outcomes; a real write failure is still a
    genuine 502): every response is a flat JSON body with "status" at the
    root — never nested under a "detail" key. HTTPException's detail=
    parameter always nests the body one level down, which required
    fragile multi-step dictionary navigation in the iOS Shortcut client
    and broke there (the candidates list silently evaluated empty). Flat
    200s remove that failure mode entirely.
      - {"status": "attached", "shortcode": ..., "notion_url": ...}
      - {"status": "needs_confirmation", "message": ..., "resource_url": ..., "candidates": [...]}
      - {"status": "not_found", "message": ...}
      - {"status": "unresolved", "message": ...}
    """
    _check_rate_limit(request)
    _check_secret(req.secret)

    if req.shortcode_or_note:
        exists, row = store.resolve_exact_shortcode(req.shortcode_or_note)
        if exists:
            if not row:
                attach_audit.record(
                    req.shortcode_or_note, req.resource_url, "not_found_wrong_status",
                )
                logger.info("attach resolved: not_found")
                return JSONResponse(
                    status_code=200,
                    content={
                        "status": "not_found",
                        "message": "this shortcode exists but isn't awaiting a DM resource right now",
                    },
                )
            if not _commit_attach(row, req.resource_url):
                attach_audit.record(
                    req.shortcode_or_note, req.resource_url, "write_failed", shortcode=row["shortcode"],
                )
                logger.info("attach resolved: failed")
                raise HTTPException(
                    status_code=502,
                    detail={
                        "status": "failed",
                        "message": "Notion write failed — attach was NOT recorded, retry",
                        "shortcode": row["shortcode"],
                    },
                )
            attach_audit.record(req.shortcode_or_note, req.resource_url, "attached", shortcode=row["shortcode"])
            logger.info("attach resolved: attached")
            return JSONResponse(
                status_code=200,
                content={"status": "attached", "shortcode": row["shortcode"], "notion_url": row["notion_page_url"]},
            )

    # No exact shortcode match (omitted, or not a real shortcode at all):
    # candidate-scoring resolution — never an auto-commit past this point.
    title, description = resource_lookup.fetch_resource_title_and_description(req.resource_url)
    candidates = store.get_attach_candidates()
    ranked = attach_matching.rank_candidates(title, description, candidates)

    if not ranked:
        attach_audit.record(req.shortcode_or_note, req.resource_url, "unresolved")
        logger.info("attach resolved: unresolved")
        return JSONResponse(
            status_code=200,
            content={
                "status": "unresolved",
                "message": "no confident match found — attach manually via an exact shortcode",
            },
        )

    summaries = [_candidate_summary(c) for c in ranked]
    attach_audit.record(
        req.shortcode_or_note, req.resource_url, "needs_confirmation",
        candidates=[c["shortcode"] for c in ranked],
    )
    logger.info("attach resolved: needs_confirmation")
    return JSONResponse(
        status_code=200,
        content={
            "status": "needs_confirmation",
            "message": "no exact shortcode — pick one of these and retry via POST /attach/confirm",
            "resource_url": req.resource_url,
            "candidates": summaries,
        },
    )


@app.post("/attach/confirm")
def attach_confirm(req: AttachConfirmRequest, request: Request) -> JSONResponse:
    """Commits a specific candidate the caller chose from a prior /attach
    "needs_confirmation" response. shortcode is REQUIRED and exact — this
    endpoint never guesses either; it only accepts a row that's still a
    genuine open attach target (see store.resolve_attachable_by_shortcode).

    Response shape: flat, always HTTP 200 for "attached"/"not_found" (same
    flattening as /attach — see its docstring); a real write failure is
    still a genuine 502."""
    _check_rate_limit(request)
    _check_secret(req.secret)

    row = store.resolve_attachable_by_shortcode(req.shortcode)
    if row is None:
        attach_audit.record(req.shortcode, req.resource_url, "confirm_not_found", shortcode=req.shortcode)
        logger.info("attach resolved: not_found")
        return JSONResponse(
            status_code=200,
            content={"status": "not_found", "message": f"{req.shortcode} is not a pending attach target"},
        )

    if not _commit_attach(row, req.resource_url):
        attach_audit.record(req.shortcode, req.resource_url, "confirm_write_failed", shortcode=req.shortcode)
        logger.info("attach resolved: failed")
        raise HTTPException(
            status_code=502,
            detail={"status": "failed", "message": "Notion write failed — attach was NOT recorded, retry"},
        )

    attach_audit.record(req.shortcode, req.resource_url, "confirmed", shortcode=req.shortcode)
    logger.info("attach resolved: attached")
    return JSONResponse(
        status_code=200,
        content={"status": "attached", "shortcode": row["shortcode"], "notion_url": row["notion_page_url"]},
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


@app.post("/daily-digest")
def daily_digest_endpoint(req: NightlyRequest, request: Request) -> JSONResponse:
    """HTTP trigger for the daily reflection digest (see PROGRESS.md) — same
    mechanism as /nightly (external scheduler, no built-in Render cron), on
    its own evening cron schedule; see SCHEDULING.md. A second, independent
    scheduled job — does not replace /nightly or the weekly digest script.
    Reuses NightlyRequest since the body shape ({secret}) is identical."""
    _check_rate_limit(request)
    _check_secret(req.secret)
    result = digest.run_daily()
    return JSONResponse(status_code=200, content=result)


@app.post("/weekly-digest")
def weekly_digest_endpoint(req: NightlyRequest, request: Request) -> JSONResponse:
    """HTTP trigger for the weekly digest (see PROGRESS.md) — same mechanism
    as /nightly and /daily-digest, on its own weekly cron schedule; see
    SCHEDULING.md. A third, independent scheduled job — does not replace
    /nightly or /daily-digest. Reuses NightlyRequest since the body shape
    ({secret}) is identical."""
    _check_rate_limit(request)
    _check_secret(req.secret)
    result = digest.run()
    return JSONResponse(status_code=200, content=result)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
