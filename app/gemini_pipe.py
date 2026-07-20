"""One Gemini call: audio -> transcript + structured extraction. BUILD_SPEC 1.3 + 1.4."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from app.fetcher import detect_comment_gate
from app.models import Extraction, ReelData, degraded_extraction

logger = logging.getLogger("reelbrain.gemini")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
# gemini-2.0-flash is no longer reliably on the free tier (returns immediate 429s).
# The current free-tier lineup is the Gemini 2.5 family — 2.5-flash is the direct
# fast/cheap replacement. Override via GEMINI_MODEL env var if needed.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest").strip()
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
# Below this many words, a caption is too thin to extract anything meaningful
# from — fall back to the honest placeholder rather than risking Gemini
# hallucinating content from almost nothing (e.g. a caption that's just a
# single hashtag or emoji).
MIN_CAPTION_WORDS_FOR_EXTRACTION = 10

# Free tier is ~10-15 RPM at <20 fetches/day total — this just prevents bursts
# if /retry and /capture happen to overlap.
_GEMINI_SEMAPHORE = threading.Semaphore(2)


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


def _call_gemini(audio_path: str, prompt: str) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_API_KEY)
    with _GEMINI_SEMAPHORE:
        uploaded = client.files.upload(file=audio_path)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[prompt, uploaded],
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
    with _GEMINI_SEMAPHORE:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[prompt],
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

    _merge_comment_gate(extraction, caption)
    extraction.priority = compute_priority(extraction.topic_tags, extraction.value_score)
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