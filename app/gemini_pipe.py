"""One Gemini call: audio -> transcript + structured extraction. BUILD_SPEC 1.3 + 1.4."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from app.fetcher import detect_comment_gate
from app.models import Extraction, ReelData, ResearchContextItem, degraded_extraction

logger = logging.getLogger("reelbrain.gemini")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
# MUST be a CONCRETE, PINNED model id — never a rolling alias like
# `gemini-flash-latest` or `gemini-flash`. INCIDENT #1 (2026-07-28): the default
# was `gemini-flash-latest`, which Google silently resolves to whatever the
# newest Flash is at call time; over a 28-day window that alias moved across
# 2.5 / 3.5 / 3.6 Flash, fragmenting quota across three independent per-model
# caps. INCIDENT #2 (2026-08-03): on a FRESH project (new key, deliberately
# non-billing-linked to restore genuine free-tier access), `gemini-2.5-flash`
# itself returned `404 "This model ... is no longer available to new users"` —
# confirmed live, not assumed. Google's ListModels endpoint still lists it (that
# API reports global model metadata, not per-project availability), so the only
# reliable check is an actual generate_content call on THIS key.
#
# Verified live against this project on 2026-08-03: gemini-2.5-flash and
# gemini-2.5-flash-lite both 404 "no longer available to new users";
# gemini-2.0-flash/-001 both 429 with the free-tier quota explicitly at
# `limit: 0` for this model. gemini-3.5-flash, gemini-3.6-flash,
# gemini-3-flash-preview, and the *-latest aliases all succeeded, including
# structured JSON output (response_schema=Extraction). gemini-3.6-flash is the
# newest NON-preview (GA) Flash id that actually works on this key — pinned
# concrete version, per the incident #1 lesson.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash").strip()
# text-embedding-004 was shut down by Google on Jan 14, 2026 — gemini-embedding-001
# is the replacement. It defaults to 3072-dim output; we pin it to 768 via
# output_dimensionality so it stays compatible with the existing sqlite-vec schema
# (DATA_SCHEMA.md §4, FLOAT[768]) without needing a storage migration.
GEMINI_EMBEDDING_MODEL = os.environ.get("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001").strip()
GEMINI_EMBEDDING_DIM = 768
PROMPT_TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "prompts" / "extraction.md"
# Photo/carousel posts (yt-dlp is video-only, can never fetch these): a lighter
# caption-only prompt, no transcription framing since there's no audio at all.
CAPTION_ONLY_PROMPT_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "extraction_caption_only.md"
)
# Carousels: every slide image is sent to Gemini in ONE multimodal call, so it
# can read the text rendered on the slides and follow the sequence. See
# app/carousel.py for how the slide URLs are obtained.
CAROUSEL_PROMPT_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "extraction_carousel.md"
)
# Below this many words, a caption is too thin to extract anything meaningful
# from — fall back to the honest placeholder rather than risking Gemini
# hallucinating content from almost nothing (e.g. a caption that's just a
# single hashtag or emoji).
MIN_CAPTION_WORDS_FOR_EXTRACTION = 10

# Phase H: when Gemini returns ZERO topic_tags, ask once more with an explicit
# demand before accepting it. One extra call, only on the empty case.
EMPTY_TOPICS_RETRY_INSTRUCTION = (
    "\n\nIMPORTANT: your previous answer returned an EMPTY topic_tags list. "
    "You MUST return 3-6 lowercase-kebab-case topic tags describing what this "
    "is about. Reuse the taxonomy candidates above where they fit. Returning "
    "an empty topic_tags list is not an acceptable answer."
)

# Free tier is ~10-15 RPM at <20 fetches/day total — this just prevents bursts
# if /retry and /capture happen to overlap.
_GEMINI_SEMAPHORE = threading.Semaphore(2)

# Distinct from fetcher.MIN_FETCH_SPACING_SECONDS, which paces yt-dlp/video
# fetches only and does nothing for Gemini's own RPM/RPD. The research pass
# (run_research_context) issues one grounded call PER topic in
# extraction.named_entities, so a single reel can now cost 1 (extraction) + up
# to MAX_RESEARCH_TOPICS Gemini calls, not a flat "2x" — see PROGRESS.md.
# Google's own free-tier numbers are inconsistent across sources and change
# without notice (confirmed: ai.google.dev points to the per-account AI Studio
# dashboard rather than publishing fixed numbers) — 4s is a conservative
# starting point (~15 RPM), override via env if your own dashboard allows more.
MIN_GEMINI_CALL_SPACING_SECONDS = float(os.environ.get("MIN_GEMINI_CALL_SPACING_SECONDS", "4"))
_LAST_GEMINI_CALL_STATE_KEY = "last_gemini_call_at"


# The SINGLE definition of what a Gemini failure means. These were previously
# copy-pasted into 7 different modules, which is exactly why the billing 429 went
# undiagnosed for days: each call site had its own idea of "quota error" and none
# of them distinguished a self-clearing rate limit from a dead account.
QUOTA_MARKERS = ("429", "RESOURCE_EXHAUSTED")
BILLING_MARKER = "prepayment credits"
_BILLING_MARKER = BILLING_MARKER  # back-compat alias
_GEMINI_LAST_ERROR_STATE_KEY = "gemini_last_error"


def is_quota_error(exc: BaseException | str) -> bool:
    """Any 429/RESOURCE_EXHAUSTED — rate limit OR billing."""
    return any(m in str(exc) for m in QUOTA_MARKERS)


def is_billing_error(exc: BaseException | str) -> bool:
    """Specifically 'prepay credits depleted' — does NOT self-clear at the next
    reset, unlike a rate limit. Needs a human to top up the account."""
    return BILLING_MARKER in str(exc)


def note_gemini_failure(exc: BaseException | str) -> None:
    """Persist a durable marker for a BILLING failure so the health watchdog can
    report 'you are out of credits' instead of the pipeline looking throttled.

    Call this from any except block that handles a Gemini error. Scripts build
    their own genai clients and never route through this module's logger, so a
    logger-only watcher silently misses them (found live: the marker stayed
    empty while every backfill was failing)."""
    try:
        if not is_billing_error(exc):
            return
        from app import store

        store.set_state(_GEMINI_LAST_ERROR_STATE_KEY, str(exc)[:500])
    except Exception:  # noqa: BLE001 - diagnostics must never break the pipeline
        pass


class _BillingWatcher(logging.Handler):
    """Persists a durable marker when Gemini reports depleted PREPAY CREDITS.

    A rate-limit 429 and a billing 429 are the same status code but opposite
    situations: the first clears itself at the next reset, the second never
    clears without a human topping up the account. Conflating them let the
    pipeline sit dead for days looking like ordinary throttling. Every call site
    reports failures through this one logger, so watching it here catches them
    all — including any added later — without touching each handler."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = record.getMessage()
            if record.exc_info and record.exc_info[1] is not None:
                text += " " + str(record.exc_info[1])
            if _BILLING_MARKER not in text:
                return
            from app import store

            store.set_state(_GEMINI_LAST_ERROR_STATE_KEY, text[:500])
        except Exception:  # noqa: BLE001 - diagnostics must never break the pipeline
            pass


logger.addHandler(_BillingWatcher())


def _enforce_gemini_call_spacing() -> None:
    """Sleeps until at least MIN_GEMINI_CALL_SPACING_SECONDS have passed since
    the last Gemini call THIS PROCESS made (extraction or research) — mirrors
    fetcher._enforce_rate_discipline's shape but tracks its own timestamp
    (app_state key, not fetch_log), since fetch spacing and Gemini spacing are
    governed by two completely different services' rate limits.

    Pure spacing only — quota RECORDING happens in generate_content_tracked,
    after the call, once the RESOLVED model is known (see that function's
    docstring for why the distinction matters)."""
    from app import store

    last_raw = store.get_state(_LAST_GEMINI_CALL_STATE_KEY)
    if last_raw:
        try:
            elapsed = time.time() - float(last_raw)
        except (TypeError, ValueError):
            elapsed = MIN_GEMINI_CALL_SPACING_SECONDS
        if elapsed < MIN_GEMINI_CALL_SPACING_SECONDS:
            time.sleep(MIN_GEMINI_CALL_SPACING_SECONDS - elapsed)
    store.set_state(_LAST_GEMINI_CALL_STATE_KEY, str(time.time()))


def _record_gemini_call_attempt(resolved_model: str) -> None:
    try:
        from app import gemini_quota

        gemini_quota.record_call(resolved_model)
    except Exception:  # noqa: BLE001 - quota bookkeeping must never break a call
        logger.debug("gemini_quota.record_call failed", exc_info=True)


def generate_content_tracked(client, model: str, contents, config=None):
    """client.models.generate_content, spaced and quota-tracked in one place.

    Records the call under the RESOLVED model version Google actually served
    (response.model_version), not the requested model string. INCIDENT
    (2026-07-28): a rolling alias (`gemini-flash-latest`) silently resolved to
    different concrete Flash versions over time, fragmenting quota across
    them. We now pin GEMINI_MODEL to a concrete version, which makes this a
    non-issue in practice — but tracking the resolved version, not the
    configured string, means the ledger stays correct even if a future default
    reverts to an alias under time pressure, rather than repeating that bug.

    On failure there is no response to resolve from, so the call is recorded
    under the ATTEMPTED model — conservative, consistent with the prior choice
    to count a rejected call rather than undercount quota usage."""
    _enforce_gemini_call_spacing()
    try:
        with _GEMINI_SEMAPHORE:
            kwargs = {"model": model, "contents": contents}
            if config is not None:
                kwargs["config"] = config
            response = client.models.generate_content(**kwargs)
    except Exception:
        _record_gemini_call_attempt(model)
        raise
    _record_gemini_call_attempt(getattr(response, "model_version", None) or model)
    return response


def _has_audio_stream(video_path: str) -> Optional[bool]:
    """ffprobe: does this file actually contain an audio stream?
    (INCIDENT: see PROGRESS.md — some Instagram downloads are video-only, and
    ffmpeg's -vn audio extraction fails outright on them.)

    Returns:
      True  — at least one audio stream present.
      False — probe succeeded and found NO audio stream.
      None  — probe couldn't run (ffprobe missing, unreadable file, etc.); the
              caller then falls through to attempting ffmpeg anyway rather than
              wrongly concluding "no audio" and skipping a fetchable transcript.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "a",  # audio streams only
                "-show_entries", "stream=index",
                "-of", "csv=p=0",
                video_path,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as exc:
        logger.warning("ffprobe audio-stream check could not run for %s: %s", video_path, exc)
        return None
    return bool(result.stdout.strip())


def _ffprobe_streams(video_path: str) -> str:
    """Human-readable stream summary (codec_type + codec_name per stream) for
    logging when ffmpeg extraction fails — makes 'why did ffmpeg fail on a file
    that exists' immediately diagnosable (audio present? what codecs?). Never
    raises; returns a short explanation if ffprobe itself can't run."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "stream=index,codec_type,codec_name",
                "-of", "csv=p=0",
                video_path,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, OSError) as exc:
        return f"(ffprobe unavailable: {exc})"
    out = (result.stdout or "").strip()
    return out or "(ffprobe reported no streams)"


def _extract_audio(video_path: str) -> str:
    """ffmpeg: strip to 16kHz mono m4a so reels <=90s upload as ~200-400KB."""
    audio_path = str(Path(video_path).with_suffix(".m4a"))
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k",
            audio_path,
        ],
        check=True,
        capture_output=True,
    )
    return audio_path


def _fill_prompt_template(template_path: Path, caption: Optional[str], creator: Optional[str],
                           note: Optional[str], taxonomy: list[str],
                           validation_errors: Optional[str] = None) -> str:
    template = template_path.read_text(encoding="utf-8")
    prompt = (
        template
        .replace("{creator}", creator or "unknown")
        .replace("{caption}", caption or "(no caption)")
        .replace("{note}", note or "(none)")
        .replace("{taxonomy}", ", ".join(taxonomy) or "(none yet)")
    )
    if validation_errors:
        prompt += (
            "\n\n## Your previous response failed schema validation\n\n"
            f"Errors:\n{validation_errors}\n\nFix these and return valid JSON only."
        )
    return prompt


def _build_prompt(caption: Optional[str], creator: Optional[str], note: Optional[str],
                   taxonomy: list[str], validation_errors: Optional[str] = None) -> str:
    return _fill_prompt_template(PROMPT_TEMPLATE_PATH, caption, creator, note, taxonomy, validation_errors)


def _build_caption_only_prompt(caption: Optional[str], creator: Optional[str], note: Optional[str],
                                taxonomy: list[str], validation_errors: Optional[str] = None) -> str:
    return _fill_prompt_template(
        CAPTION_ONLY_PROMPT_TEMPLATE_PATH, caption, creator, note, taxonomy, validation_errors
    )


def _build_carousel_prompt(caption: Optional[str], creator: Optional[str], note: Optional[str],
                           taxonomy: list[str], slide_count: int,
                           validation_errors: Optional[str] = None) -> str:
    text = _fill_prompt_template(
        CAROUSEL_PROMPT_TEMPLATE_PATH, caption, creator, note, taxonomy, validation_errors
    )
    return text.replace("{slide_count}", str(slide_count))


def _call_gemini_carousel(prompt: str, images: list[bytes]) -> str:
    """ONE multimodal call carrying every slide, in order, alongside the
    prompt. Deliberately a single call rather than one-per-slide: the whole
    value is Gemini seeing the SEQUENCE (slide 3 only means something in the
    context of 1 and 2), and it also costs one request instead of N against a
    20-requests/day free tier."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_API_KEY)
    parts: list = [prompt]
    for image in images:
        parts.append(types.Part.from_bytes(data=image, mime_type="image/jpeg"))
    response = generate_content_tracked(
        client, GEMINI_MODEL, contents=parts,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=Extraction,
        ),
    )
    return response.text


def run_carousel_extraction(
    images: list[bytes], caption: Optional[str], creator: Optional[str],
    note: Optional[str], taxonomy: list[str],
) -> Optional[Extraction]:
    """Multimodal carousel extraction — Gemini READS the slides.

    Returns None (never a placeholder) if there are no images or the call
    fails, so the caller can fall back to caption-only and log WHY. Same
    degrade-honestly contract as run_resource_extraction.
    """
    if not images:
        logger.info("run_carousel_extraction: no slide images supplied — caller should fall back")
        return None

    prompt = _build_carousel_prompt(caption, creator, note, taxonomy, len(images))

    for attempt in range(2):  # one retry, same convention as the other paths
        try:
            raw = _call_gemini_carousel(prompt, images)
            extraction = _parse(raw)
            _merge_comment_gate(extraction, caption)
            extraction.priority = compute_priority(extraction.topic_tags, extraction.value_score)
            return extraction
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.warning(
                "run_carousel_extraction: schema validation failed (attempt %d/2): %s", attempt + 1, exc,
            )
            prompt = _build_carousel_prompt(
                caption, creator, note, taxonomy, len(images), validation_errors=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 - network/API errors get no retry budget
            logger.exception(
                "run_carousel_extraction: Gemini call failed (attempt %d/2): %s", attempt + 1, exc,
            )
            break

    logger.warning(
        "run_carousel_extraction: giving up after exhausting attempts — caller must fall back to caption-only",
    )
    return None


def _call_gemini(audio_path: str, prompt: str) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_API_KEY)
    with _GEMINI_SEMAPHORE:
        uploaded = client.files.upload(file=audio_path)
    response = generate_content_tracked(
        client, GEMINI_MODEL, contents=[prompt, uploaded],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=Extraction,
        ),
    )
    return response.text


def _call_gemini_text_only(prompt: str) -> str:
    """Same structured-output call as _call_gemini, but no audio/video upload —
    used for photo/carousel posts, where only the caption is ever available."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_API_KEY)
    response = generate_content_tracked(
        client, GEMINI_MODEL, contents=[prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=Extraction,
        ),
    )
    return response.text


def embed_text(text: str) -> list[float]:
    """BUILD_SPEC 3.1: Gemini embedding free tier, pinned to 768-dim output for
    compatibility with the existing sqlite-vec schema. Raises on any failure —
    callers treat embeddings as an enhancement and skip on error, not a hard dependency."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_API_KEY)
    with _GEMINI_SEMAPHORE:
        response = client.models.embed_content(
            model=GEMINI_EMBEDDING_MODEL,
            contents=text,
            config=types.EmbedContentConfig(output_dimensionality=GEMINI_EMBEDDING_DIM),
        )
    return list(response.embeddings[0].values)


def _decode(text_or_bytes) -> str:
    """subprocess.run(capture_output=True) hands back bytes; ffmpeg's stderr is
    what actually says WHY it failed, so this must survive into the log even
    when it's not valid UTF-8 (ffmpeg output isn't guaranteed clean text)."""
    if isinstance(text_or_bytes, bytes):
        return text_or_bytes.decode("utf-8", errors="replace")
    return text_or_bytes or ""


def _parse(raw: str) -> Extraction:
    data = json.loads(raw)
    return Extraction.model_validate(data)


def _degraded(caption: Optional[str]) -> Extraction:
    """A degraded extraction that STILL runs comment-gate detection. The gate check
    is a pure caption regex needing no AI, so it must survive every failure path —
    otherwise a gated reel whose Gemini call died would silently lose its keyword."""
    extraction = degraded_extraction(caption)
    _merge_comment_gate(extraction, caption)
    extraction.priority = compute_priority(extraction.topic_tags, extraction.value_score)
    return extraction


def _check_video_file_size(video_path: str, expected_size: Optional[int]) -> Optional[str]:
    """Defensive check (see PROGRESS.md's timing-bug investigation): confirms
    the file on disk actually matches the size yt-dlp itself reported as
    expected, BEFORE ffmpeg ever touches it. Returns an explanatory message if
    it looks truncated/missing, or None if it's fine (or there's nothing to
    compare against — not every format gets a reported size from yt-dlp).
    Converts a silently-truncated file into an explicit, actionable log line
    instead of an opaque ffmpeg CalledProcessError downstream."""
    if not expected_size:
        return None
    try:
        actual_size = os.path.getsize(video_path)
    except OSError as exc:
        return f"video file {video_path} is missing or unreadable ({exc})"
    if actual_size < expected_size:
        pct = actual_size / expected_size * 100
        return (
            f"video file {video_path} looks truncated: {actual_size} bytes on disk vs "
            f"{expected_size} expected ({pct:.1f}%) — likely still being written"
        )
    return None


def run_extraction(
    reel: ReelData, note: Optional[str], taxonomy: list[str]
) -> Extraction:
    """Returns a validated Extraction, degrading gracefully rather than raising.

    INCIDENT (see PROGRESS.md): every degradation point here used to be
    completely silent — a successfully-downloaded video could land as a bare
    caption placeholder (no Topics, flat value_score=3) with zero indication
    anywhere of why. Every branch that calls _degraded() now logs the actual
    exception first — this must never be invisible again.
    """
    if not reel.video_path:
        # FIX 1 (see PROGRESS.md): this branch used to go straight to the bare
        # _degraded() placeholder — caption stored raw as the title, no Topics,
        # flat value_score — even though run_caption_only_extraction already
        # existed and demonstrably produces real Topics/value_score/synthesized
        # titles from a caption alone (proven live on the no-audio path).
        # Route through it whenever a caption exists, exactly like the no-audio
        # branch below; run_caption_only_extraction itself falls back to the
        # placeholder when the caption is missing or too thin to summarize.
        logger.warning(
            "run_extraction: no video_path for %s — running caption-only "
            "extraction (expected for an OG-tag-recovered reel; a problem if "
            "this reel should have had a real video download)",
            reel.shortcode,
        )
        return run_caption_only_extraction(reel.caption, reel.creator_username, note, taxonomy)

    size_issue = _check_video_file_size(reel.video_path, reel.expected_video_size)
    if size_issue:
        logger.warning(
            "run_extraction: %s for %s — degrading to caption-only without "
            "calling ffmpeg", size_issue, reel.shortcode,
        )
        return _degraded(reel.caption)

    # No audio stream at all (INCIDENT: see PROGRESS.md — some IG downloads are
    # video-only, and ffmpeg's -vn extraction then fails with exit 1). This is
    # NOT a technical failure: there's genuinely nothing to transcribe, so route
    # to a real caption-only extraction with a distinct note, not the generic
    # degrade. _has_audio_stream returns None ("couldn't probe") -> fall through
    # and let ffmpeg try, preserving prior behavior when ffprobe is unavailable.
    if _has_audio_stream(reel.video_path) is False:
        logger.warning(
            "run_extraction: no audio stream in %s (video_path=%s) — routing to "
            "caption-only extraction instead of failing on ffmpeg -vn",
            reel.shortcode, reel.video_path,
        )
        reel.fetch_note = "no audio track in source video — summarized from caption only, no transcript"
        return run_caption_only_extraction(reel.caption, reel.creator_username, note, taxonomy)

    try:
        audio_path = _extract_audio(reel.video_path)
    except subprocess.CalledProcessError as exc:
        logger.exception(
            "run_extraction: ffmpeg audio extraction failed for %s "
            "(video_path=%s, returncode=%s) — degrading to caption-only. "
            "stderr: %s | ffprobe streams: %s",
            reel.shortcode, reel.video_path, exc.returncode,
            _decode(exc.stderr)[:2000], _ffprobe_streams(reel.video_path),
        )
        return _degraded(reel.caption)

    prompt = _build_prompt(reel.caption, reel.creator_username, note, taxonomy)

    extraction: Optional[Extraction] = None
    validation_errors: Optional[str] = None
    for attempt in range(2):  # one retry, per BUILD_SPEC 1.4
        try:
            raw = _call_gemini(audio_path, prompt)
            extraction = _parse(raw)
            break
        except (json.JSONDecodeError, ValidationError) as exc:
            validation_errors = str(exc)
            logger.warning(
                "run_extraction: Gemini response failed schema validation for %s "
                "(attempt %d/2): %s", reel.shortcode, attempt + 1, validation_errors,
            )
            prompt = _build_prompt(
                reel.caption, reel.creator_username, note, taxonomy,
                validation_errors=validation_errors,
            )
        except Exception as exc:  # noqa: BLE001 - network/API errors: no retry budget for these
            logger.exception(
                "run_extraction: Gemini extraction call failed for %s (attempt %d/2): %s "
                "— degrading to caption-only", reel.shortcode, attempt + 1, exc,
            )
            break

    if extraction is None:
        logger.warning(
            "run_extraction: degrading to caption-only for %s after exhausting "
            "extraction attempts (see the error logged above for why)", reel.shortcode,
        )
        return _degraded(reel.caption)

    _merge_comment_gate(extraction, reel.caption)
    extraction.priority = compute_priority(extraction.topic_tags, extraction.value_score)
    extraction.research_context = run_research_context(extraction.named_entities)
    return extraction


def run_caption_only_extraction(
    caption: Optional[str], creator: Optional[str], note: Optional[str], taxonomy: list[str]
) -> Extraction:
    """Photo/carousel posts: yt-dlp can never fetch these (video-only), but a
    caption is often still recoverable via the OG-tag fallback (fetcher.py).
    Rather than storing that caption raw as a bare placeholder, this runs it
    through the SAME structured extraction Gemini call as a normal reel — just
    without any audio/video upload — so these rows get a real Main Point,
    Topics, Value score, and Priority instead of being permanently
    second-class. Degrades gracefully rather than raising, same contract as
    run_extraction.

    A caption below MIN_CAPTION_WORDS_FOR_EXTRACTION words is too thin to
    extract anything meaningful from — falls back to the honest placeholder
    rather than risking Gemini hallucinating content from almost nothing.
    """
    if not caption or len(caption.split()) < MIN_CAPTION_WORDS_FOR_EXTRACTION:
        logger.info(
            "run_caption_only_extraction: caption too thin (%d words) for %r — "
            "degrading to placeholder without calling Gemini",
            len((caption or "").split()), (caption or "")[:80],
        )
        return _degraded(caption)

    prompt = _build_caption_only_prompt(caption, creator, note, taxonomy)

    extraction: Optional[Extraction] = None
    validation_errors: Optional[str] = None
    for attempt in range(2):  # one retry, per BUILD_SPEC 1.4's existing convention
        try:
            raw = _call_gemini_text_only(prompt)
            extraction = _parse(raw)
            break
        except (json.JSONDecodeError, ValidationError) as exc:
            validation_errors = str(exc)
            logger.warning(
                "run_caption_only_extraction: Gemini response failed schema "
                "validation for caption %r (attempt %d/2): %s",
                caption[:80], attempt + 1, validation_errors,
            )
            prompt = _build_caption_only_prompt(
                caption, creator, note, taxonomy, validation_errors=validation_errors,
            )
        except Exception as exc:  # noqa: BLE001 - network/API errors: no retry budget for these
            logger.exception(
                "run_caption_only_extraction: Gemini call failed for caption %r "
                "(attempt %d/2): %s — degrading to placeholder",
                caption[:80], attempt + 1, exc,
            )
            break

    if extraction is None:
        logger.warning(
            "run_caption_only_extraction: degrading to placeholder for caption %r "
            "after exhausting extraction attempts (see the error logged above for why)",
            caption[:80],
        )
        return _degraded(caption)

    extraction = _retry_if_topics_empty(extraction, prompt, _call_gemini_text_only)
    _merge_comment_gate(extraction, caption)
    extraction.priority = compute_priority(extraction.topic_tags, extraction.value_score)
    extraction.research_context = run_research_context(extraction.named_entities)
    return extraction


# Below this many words, fetched resource text is too thin to summarize
# meaningfully (e.g. a near-empty page, a repo with a one-line README) --
# same anti-hallucination reasoning as MIN_CAPTION_WORDS_FOR_EXTRACTION.
MIN_RESOURCE_WORDS_FOR_EXTRACTION = 30
# Gemini's context is plenty for this, but there's no reason to upload an
# entire multi-hundred-page PDF for a summary -- cap what actually gets sent.
MAX_RESOURCE_CHARS = 40_000

_RESOURCE_PROMPT_TEMPLATE = """You are summarizing a long-form resource that was DM'd to someone in \
response to a "comment X for the link" offer on an Instagram reel, for a personal knowledge base. \
The resource is a {resource_kind} attached to a reel titled: "{reel_title}"

Anti-slop rules (follow strictly):
- `summary` is 2-4 sentences of plain prose covering what this resource actually contains and why it's worth revisiting -- not a restatement of the reel's title.
- `key_takeaways` are 0-8 concrete, specific points (a real technique, a named tool, a number) -- never generic filler like "this is useful information". Zero is correct if there is genuinely nothing concrete to extract.
- `topic_tags`: 3-6 lowercase-kebab-case tags. Prefer these existing tags when they genuinely fit: {taxonomy}. Only introduce a new tag if none fit.
- `resource_kind` must be exactly one of: github_repo, google_doc, web_article, pdf, other -- classify based on the content itself, not just the URL shape.
- `suggested_action`: ONE imperative line stating the single most direct next step a reader could take with this resource (e.g. "Clone the repo and run the demo", "Copy the prompts into a Claude Project"). If it is purely informational with nothing to act on, use exactly "none — informational". Never more than one sentence, never vague.
- Never invent content that isn't in the text below. If the resource is thin or mostly boilerplate, say so honestly in `summary` rather than padding.

Return only the structured JSON, no markdown, no commentary.

--- RESOURCE CONTENT ---
{content}
--- END RESOURCE CONTENT ---
"""


def _build_resource_prompt(content: str, resource_kind: str, reel_title: str, taxonomy: list[str]) -> str:
    return _RESOURCE_PROMPT_TEMPLATE.format(
        resource_kind=resource_kind,
        reel_title=reel_title or "(untitled)",
        taxonomy=", ".join(taxonomy) or "(none yet)",
        content=content[:MAX_RESOURCE_CHARS],
    )


def _call_gemini_resource(prompt: str):
    from google import genai
    from google.genai import types

    from app.models import ResourceExtraction

    client = genai.Client(api_key=GEMINI_API_KEY)
    response = generate_content_tracked(
        client, GEMINI_MODEL, contents=[prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ResourceExtraction,
        ),
    )
    return response.text


def run_resource_extraction(
    content: str, resource_kind: str, reel_title: str, taxonomy: list[str]
):
    """Summarizes a long-form DM'd resource (Drive doc, GitHub README, web
    guide, PDF text) -- adapted from run_caption_only_extraction for
    longer-form content. Returns None (never raises, never a placeholder) on
    ANY failure: too-thin content, schema validation failure after retry, or
    a Gemini API error. Callers must treat None as "don't write this, retry
    later" -- same degraded-extraction discipline as the reel pipeline, since
    writing a hallucinated or empty summary would be worse than not writing
    at all."""
    from app.models import ResourceExtraction

    if not content or len(content.split()) < MIN_RESOURCE_WORDS_FOR_EXTRACTION:
        logger.info(
            "run_resource_extraction: content too thin (%d words) for %r — skipping Gemini call",
            len((content or "").split()), reel_title,
        )
        return None

    prompt = _build_resource_prompt(content, resource_kind, reel_title, taxonomy)

    for attempt in range(2):  # one retry, same convention as the reel extraction paths
        try:
            raw = _call_gemini_resource(prompt)
            return ResourceExtraction.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.warning(
                "run_resource_extraction: schema validation failed for %r (attempt %d/2): %s",
                reel_title, attempt + 1, exc,
            )
        except Exception as exc:  # noqa: BLE001 - network/API errors: no retry budget for these
            logger.exception(
                "run_resource_extraction: Gemini call failed for %r (attempt %d/2): %s",
                reel_title, attempt + 1, exc,
            )
            break

    logger.warning(
        "run_resource_extraction: giving up for %r after exhausting attempts "
        "(see the error logged above for why) — caller must not write this", reel_title,
    )
    return None


# --- research pass: Gemini call 2, one grounded call PER named entity ---------
#
# Deliberately NOT one batched call for every entity: Gemini's response_schema/
# controlled-generation is documented as incompatible with the google_search
# tool ("Search Grounding can't be used with JSON/YAML/XML mode" — confirmed
# against the Gemini API forum/GitHub issues, see PROGRESS.md), and even
# without that constraint, a single multi-entity call only exposes
# grounding_metadata at the WHOLE-response level — there'd be no way to tell
# WHICH entity search actually found something for for. One call per entity
# gives an unambiguous per-entity signal instead, at the cost of real Gemini
# call volume (see MIN_GEMINI_CALL_SPACING_SECONDS above and PROGRESS.md).

NOT_FOUND_VIA_SEARCH = "not found via search"
# Matches the extraction schema's own upper bound on named_entities/topic_tags
# (3-6) — a safety cap, not expected to actually trim anything in practice.
MAX_RESEARCH_ENTITIES = 6

_RESEARCH_PROMPT_TEMPLATE = (
    'In 2-3 sentences, explain what "{entity}" is and why it matters, based on '
    "real, current search results — not your own general knowledge. If you "
    'genuinely can\'t find anything specific about "{entity}" via search, say '
    "so plainly rather than describing it from memory."
)


def _call_gemini_research(entity: str):
    """ONE grounded call for ONE named entity. No response_schema (see the
    module-level note above) — the caller already knows the entity name, so
    the reply is treated as the context prose directly, not parsed as JSON."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_API_KEY)
    return generate_content_tracked(
        client, GEMINI_MODEL, contents=[_RESEARCH_PROMPT_TEMPLATE.format(entity=entity)],
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
        ),
    )


_WEBFETCH_CONTEXT_PROMPT = """Using ONLY the material below (fetched live from {url}), \
write 2-3 sentences: what is "{entity}" and why does it matter? Restate only what the \
material actually says — never add facts from your own knowledge. If the material does \
not actually describe "{entity}", answer exactly: not found via search

--- FETCHED MATERIAL ---
{material}
--- END FETCHED MATERIAL ---
"""


def _call_gemini_webfetch_context(entity: str, material: str, url: str) -> str:
    """Plain (ungrounded) Gemini call that may ONLY restate genuinely-fetched
    material — the free fallback when Search grounding isn't available on this
    API key. The honesty guarantee holds because the material itself was
    fetched for real (app/web_research.py), so nothing here is unverified
    model memory presented as verified."""
    from google import genai

    client = genai.Client(api_key=GEMINI_API_KEY)
    response = generate_content_tracked(
        client, GEMINI_MODEL,
        contents=[_WEBFETCH_CONTEXT_PROMPT.format(entity=entity, material=material, url=url)],
    )
    return (response.text or "").strip()


def _research_via_web_fetch(entity: str) -> "ResearchContextItem":
    """The fallback path for one entity: fetch real material (DDG top result /
    GitHub README), summarize it, mark source \"web-fetch\". No material means
    the honest not-found marker — never a training-data guess."""
    from app import web_research

    material, url = web_research.fetch_context_material(entity)
    if not material:
        return ResearchContextItem(topic=entity, context=NOT_FOUND_VIA_SEARCH, source="web-fetch")
    text = _call_gemini_webfetch_context(entity, material, url)
    return ResearchContextItem(topic=entity, context=text or NOT_FOUND_VIA_SEARCH, source="web-fetch")


def _grounding_found_results(response) -> bool:
    """True only if Google Search grounding actually returned at least one
    supporting chunk for this call. This is the whole point of point 2 in the
    spec: if this is False, the caller must NOT use response.text (Gemini can
    and does answer from its own training data even when grounding came back
    empty) — it must write the literal NOT_FOUND_VIA_SEARCH marker instead."""
    try:
        candidate = response.candidates[0]
        chunks = candidate.grounding_metadata.grounding_chunks
    except (AttributeError, IndexError, TypeError):
        return False
    return bool(chunks)


def run_research_context(named_entities: list[str]) -> list[ResearchContextItem]:
    """Gemini call 2: for each named entity from call 1's extraction, a
    search-grounded lookup producing a short "what it is / why it matters"
    writeup — filling in what the reel itself didn't explain. NEVER lets a
    zero-result search silently fall back to Gemini's own training data
    dressed up as verified (see _grounding_found_results). Best-effort per
    entity: one entity's failure never blocks the others or raises, and an
    empty/None input just returns an empty list without calling Gemini at all."""
    results: list[ResearchContextItem] = []
    grounding_blocked = False  # after one 429 on a grounded call, don't keep burning them
    for entity in (named_entities or [])[:MAX_RESEARCH_ENTITIES]:
        if grounding_blocked:
            response = None
        else:
            try:
                response = _call_gemini_research(entity)
            except Exception as exc:  # noqa: BLE001 - grounding unavailable/blocked -> fallback
                logger.warning(
                    "run_research_context: grounded call failed for entity %r "
                    "— trying the web-fetch fallback", entity, exc_info=True,
                )
                if "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc):
                    grounding_blocked = True
                response = None
        if response is None:
            # Free fallback: real fetched material (DDG/GitHub), source
            # "web-fetch" — or the honest not-found marker. Best-effort: a
            # fallback failure just skips this entity.
            try:
                results.append(_research_via_web_fetch(entity))
            except Exception:  # noqa: BLE001
                logger.warning(
                    "run_research_context: web-fetch fallback failed for %r", entity, exc_info=True,
                )
            continue
        if not _grounding_found_results(response):
            logger.info(
                "run_research_context: no search results grounded for %r — "
                "writing the honest not-found marker, not a training-data guess", entity,
            )
            results.append(ResearchContextItem(topic=entity, context=NOT_FOUND_VIA_SEARCH))
            continue
        text = (response.text or "").strip()
        results.append(ResearchContextItem(topic=entity, context=text or NOT_FOUND_VIA_SEARCH))
    return results


def _retry_if_topics_empty(extraction, prompt: str, call_fn):
    """One extra Gemini call when topic_tags came back empty (Phase H).
    Returns the better of the two extractions. Never raises — on any failure
    the original extraction is kept and the notion_writer guard still applies
    the derived fallback, so a row can never be written topic-less."""
    if extraction.topic_tags:
        return extraction
    logger.warning("extraction returned zero topic_tags — retrying once with an explicit demand")
    try:
        retried = _parse(call_fn(prompt + EMPTY_TOPICS_RETRY_INSTRUCTION))
    except Exception:  # noqa: BLE001 - best effort; fallback chain covers us
        logger.warning("topic retry failed; the derive-fallback guard will apply", exc_info=True)
        return extraction
    if retried.topic_tags:
        logger.info("topic retry succeeded: %s", retried.topic_tags)
        return retried
    return extraction


def _merge_comment_gate(extraction: Extraction, caption: Optional[str]) -> None:
    """BUILD_SPEC 1.4: regex pre-check merged with the model's own field — either positive -> gated."""
    regex_keyword = detect_comment_gate(caption)
    if regex_keyword and not extraction.comment_gate.detected:
        extraction.comment_gate.detected = True
        extraction.comment_gate.keyword = extraction.comment_gate.keyword or regex_keyword
    elif regex_keyword and not extraction.comment_gate.keyword:
        extraction.comment_gate.keyword = regex_keyword

    # INVARIANT (BUG 2 incident: DajFASZODlj had gate_keyword="International"
    # but comment_gate=False; DaQIJHnP6zn had gate_keyword="CODING" with the
    # same mismatch). Both fields describe the same fact and must never
    # disagree. The mismatch wasn't introduced by the merge logic above — it's
    # Gemini's own structured output occasionally setting a keyword while
    # leaving detected=False, and when the regex ALSO finds nothing (a gate
    # phrasing our patterns don't cover), nothing here used to correct it. A
    # keyword is a stronger signal than the model's own boolean, so force
    # agreement here, at the single point both fields are finalized.
    if extraction.comment_gate.keyword and not extraction.comment_gate.detected:
        extraction.comment_gate.detected = True
    assert extraction.comment_gate.detected or not extraction.comment_gate.keyword, (
        "comment_gate invariant violated: keyword set without detected=True"
    )


# --- Priority: a computed field driving real action, not decoration ----------
#
# value_score alone was decorative — nothing acted on it. Priority turns it (plus
# a Claude/Anthropic-relevance signal, since that's what actually needs a quick
# follow-up) into something the "🎯 Action Needed" Notion view and the Obsidian
# vault's priority-first index both filter/sort on. Named constant so the
# keyword list is easy to extend later without touching the matching logic.
CLAUDE_KEYWORDS = ("claude", "claude-ai", "claude-code", "anthropic", "claude-skills", "mcp")


def _is_claude_related(topic_tags: list[str]) -> bool:
    """Case-insensitive substring match: any keyword appearing anywhere inside
    any topic tag counts (e.g. topic 'claude-code-tips' matches both 'claude'
    and 'claude-code')."""
    return any(keyword in tag.lower() for tag in topic_tags for keyword in CLAUDE_KEYWORDS)


def compute_priority(topic_tags: list[str], value_score: int) -> str:
    """'High' if either a Claude/Anthropic-related topic is present or
    value_score >= 4; 'Medium' if value_score == 3; 'Low' otherwise. Plain text
    values only ("High"/"Medium"/"Low") — no emoji, since this drives a Notion
    Select property and Obsidian frontmatter directly."""
    if _is_claude_related(topic_tags) or value_score >= 4:
        return "High"
    if value_score == 3:
        return "Medium"
    return "Low"