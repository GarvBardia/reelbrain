"""One Gemini call: audio -> transcript + structured extraction. BUILD_SPEC 1.3 + 1.4."""
from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from app.fetcher import detect_comment_gate
from app.models import Extraction, ReelData, degraded_extraction

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
# gemini-2.0-flash is no longer reliably on the free tier (returns immediate 429s).
# The current free-tier lineup is the Gemini 2.5 family — 2.5-flash is the direct
# fast/cheap replacement. Override via GEMINI_MODEL env var if needed.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
# text-embedding-004 was shut down by Google on Jan 14, 2026 — gemini-embedding-001
# is the replacement. It defaults to 3072-dim output; we pin it to 768 via
# output_dimensionality so it stays compatible with the existing sqlite-vec schema
# (DATA_SCHEMA.md §4, FLOAT[768]) without needing a storage migration.
GEMINI_EMBEDDING_MODEL = os.environ.get("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
GEMINI_EMBEDDING_DIM = 768
PROMPT_TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "prompts" / "extraction.md"

# Free tier is ~10-15 RPM at <20 fetches/day total — this just prevents bursts
# if /retry and /capture happen to overlap.
_GEMINI_SEMAPHORE = threading.Semaphore(2)


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


def _build_prompt(caption: Optional[str], creator: Optional[str], note: Optional[str],
                   taxonomy: list[str], validation_errors: Optional[str] = None) -> str:
    template = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
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


def _parse(raw: str) -> Extraction:
    data = json.loads(raw)
    return Extraction.model_validate(data)


def run_extraction(
    reel: ReelData, note: Optional[str], taxonomy: list[str]
) -> Extraction:
    """Returns a validated Extraction, degrading gracefully rather than raising."""
    if not reel.video_path:
        return degraded_extraction(reel.caption)

    try:
        audio_path = _extract_audio(reel.video_path)
    except subprocess.CalledProcessError:
        return degraded_extraction(reel.caption)

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
            prompt = _build_prompt(
                reel.caption, reel.creator_username, note, taxonomy,
                validation_errors=validation_errors,
            )
        except Exception:  # noqa: BLE001 - network/API errors: no retry budget for these
            break

    if extraction is None:
        return degraded_extraction(reel.caption)

    _merge_comment_gate(extraction, reel.caption)
    return extraction


def _merge_comment_gate(extraction: Extraction, caption: Optional[str]) -> None:
    """BUILD_SPEC 1.4: regex pre-check merged with the model's own field — either positive -> gated."""
    regex_keyword = detect_comment_gate(caption)
    if regex_keyword and not extraction.comment_gate.detected:
        extraction.comment_gate.detected = True
        extraction.comment_gate.keyword = extraction.comment_gate.keyword or regex_keyword
    elif regex_keyword and not extraction.comment_gate.keyword:
        extraction.comment_gate.keyword = regex_keyword