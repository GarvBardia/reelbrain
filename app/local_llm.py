"""Local LLM provider: Ollama (http://localhost:11434), text-only.

WHY THIS EXISTS (PROGRESS.md, 2026-08-16): the Gemini free tier caps out around
17-20 calls/day and has repeatedly blocked backlog work for days at a time. The
user is NOT adding Gemini billing, ever -- a local model is the PERMANENT fix
for text-only backlog work, not a stopgap. Confirmed working on this machine:
CPU-only AMD laptop, no CUDA, llama3.1:8b via Ollama, ~sub-second for a trivial
reply, real extraction-length prompts take longer but still comfortably under
a minute in practice.

Text-only, on purpose: Ollama here has no audio transcription or vision input
for this model, so anything needing the reel's actual audio/video/slide images
(full extraction, carousel slide reading, recover_placeholders) MUST stay on
Gemini -- see app/llm_router.py's TASK_PROVIDERS for the routing decision.

This module knows nothing about Gemini, quota tracking, or task routing --
that's app/llm_router.py's job. This is just "can we talk to Ollama."
"""
from __future__ import annotations

import os

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434").strip()
LOCAL_LLM_MODEL = os.environ.get("LOCAL_LLM_MODEL", "llama3.1:8b").strip()
# A trivial reply came back in well under a second live on this machine, but a
# real extraction-shaped prompt (schema-heavy, longer context) will take
# noticeably longer -- 90-120s avoids a false failure on a legitimately slow
# but working generation, matching the margin GEMINI_MAX_OUTPUT_TOKENS gave
# the Gemini path for the analogous "don't false-fail on real work" reason.
LOCAL_LLM_TIMEOUT_SECONDS = float(os.environ.get("LOCAL_LLM_TIMEOUT_SECONDS", "120"))


class OllamaUnavailable(Exception):
    """Ollama isn't running or isn't reachable at OLLAMA_HOST (connection
    refused, DNS failure, etc.) -- as opposed to Ollama being reachable but
    slow/erroring, which is an ordinary failure a caller's own retry logic
    should handle.

    HARD BOUNDARY (PROGRESS.md Phase 5): a caller MUST treat this as "skip
    this item for this run," never as a signal to silently fall back to
    Gemini -- that would burn the exact quota this provider exists to
    protect. There is deliberately no automatic-fallback code path anywhere
    in this module or app/llm_router.py."""


def generate(prompt: str, *, json_mode: bool = False) -> str:
    """One text completion via Ollama's /api/generate (non-streaming).

    Raises OllamaUnavailable when Ollama itself can't be reached. Raises
    httpx's own exceptions (TimeoutException, HTTPStatusError) for a
    reachable-but-failing Ollama -- callers should treat those as an
    ordinary, retryable failure, not a "provider is down" signal."""
    import httpx

    payload: dict = {
        "model": LOCAL_LLM_MODEL,
        "prompt": prompt,
        "stream": False,
    }
    if json_mode:
        payload["format"] = "json"

    try:
        response = httpx.post(
            f"{OLLAMA_HOST}/api/generate", json=payload, timeout=LOCAL_LLM_TIMEOUT_SECONDS,
        )
    except httpx.ConnectError as exc:
        raise OllamaUnavailable(
            f"Ollama not reachable at {OLLAMA_HOST} -- is `ollama serve` running? ({exc})"
        ) from exc

    response.raise_for_status()
    return response.json().get("response", "")
