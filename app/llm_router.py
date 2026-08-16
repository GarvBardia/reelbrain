"""Single choke point deciding which LLM provider handles a given TEXT-ONLY
task -- Gemini (quota-tracked, multimodal-capable) or local Ollama (free,
unlimited, slower, text-only). See PROGRESS.md, 2026-08-16: the user is never
adding Gemini billing, so local is the permanent fix for the free-tier quota
bottleneck on text-only backlog work, not a stopgap.

TASK_PROVIDERS is the single source of truth for routing -- change a task's
provider here, not by editing call sites. Every text-only Gemini/local call in
the codebase should route through generate_text/generate_json here rather than
building its own genai.Client or calling app.local_llm directly, so routing
decisions and Ollama's hard fallback boundary live in exactly one place.

HARD BOUNDARY (Phase 5): a local-routed call that fails because Ollama isn't
reachable raises app.local_llm.OllamaUnavailable and STOPS there -- there is no
except-and-retry-on-Gemini anywhere in this module. Falling back to Gemini
would silently burn the exact quota this router exists to protect. Callers
(the batch scripts) already have their own "skip this row, keep going" /
"stop the whole run" handling for exceptions; OllamaUnavailable just flows
into that same path as an ordinary failure.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger("reelbrain.llm_router")

GEMINI = "gemini"
LOCAL = "local"

# Multimodal tasks (audio transcription, slide-image vision) MUST stay on
# Gemini -- app.local_llm is text-only. Everything below is deliberately
# routed to LOCAL to keep it off the Gemini quota the user is protecting;
# see PROGRESS.md Phase 4 for the honest per-task quality comparison behind
# this specific set.
TASK_PROVIDERS: dict[str, str] = {
    # stays on Gemini -- needs real multimodal input
    "full_extraction": GEMINI,
    "caption_only_extraction": GEMINI,
    "carousel_extraction": GEMINI,
    "recover_placeholders": GEMINI,
    "research_context_grounded": GEMINI,  # needs the google_search tool
    # PROGRESS.md 2026-08-16 Phase 4 (live 3-row comparison, llama3.1:8b):
    # suggested_action_backfill stays on GEMINI -- local quality was
    # materially worse (one answer was flatly wrong -- an apparently
    # hallucinated "reply with a comment keyword" for a title with no
    # comment-gate at all; the other two were generic where Gemini's were
    # specific and actionable). Shipping that into the corpus would be a
    # regression, not a fix, so this one task is deliberately NOT local-routed
    # despite being text-only. The other four passed the same comparison.
    "suggested_action_backfill": GEMINI,
    "plain_summary_backfill": LOCAL,
    "topic_retag": LOCAL,
    "notion_deep_clean_tagging": LOCAL,
    "research_context_web_fetch": LOCAL,
}


def provider_for(task: str) -> str:
    """Unknown task names default to Gemini -- the safer failure mode (a
    quota-tracked call, not a silently-untracked local one) if a call site is
    ever added here without a TASK_PROVIDERS entry."""
    return TASK_PROVIDERS.get(task, GEMINI)


# Appended to the prompt on a validation-failure retry -- the same "be more
# careful" nudge the Gemini extraction path already used for its own
# empty-topics retry (gemini_pipe.EMPTY_TOPICS_RETRY_INSTRUCTION), generalized
# to any schema failure. Smaller local models are measurably less reliable at
# strict JSON than Gemini (see PROGRESS.md Phase 4), so this retry matters
# more on the local path, but it applies to either provider.
STRICT_JSON_RETRY_SUFFIX = (
    "\n\nIMPORTANT: your previous answer was not valid JSON matching the "
    "required shape. Return ONLY the JSON object -- no markdown, no code "
    "fences, no commentary before or after it. Every required field must be "
    "present and correctly typed."
)


def generate_text(task: str, prompt: str, *, response_schema=None) -> str:
    """Route ONE text-only generation call to whichever provider TASK_PROVIDERS
    says for `task`. Returns raw text (parsing/validation is the caller's or
    generate_json's job).

    `response_schema` only affects the Gemini path (its native structured-
    output mode); the local path just requests Ollama's "format": "json" mode
    whenever a schema is given, since Ollama has no schema-aware mode -- the
    caller is responsible for validating the result either way."""
    provider = provider_for(task)
    if provider == LOCAL:
        return _generate_local(prompt, json_mode=response_schema is not None)
    return _generate_gemini(prompt, response_schema=response_schema)


def _generate_gemini(prompt: str, *, response_schema=None) -> str:
    from google import genai
    from google.genai import types

    from app import gemini_pipe

    client = genai.Client(api_key=gemini_pipe.GEMINI_API_KEY)
    config = None
    if response_schema is not None:
        config = types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=response_schema,
        )
    # gemini_pipe.generate_content_tracked, not client.models.generate_content
    # directly -- it records quota under the RESOLVED model version Google
    # actually served, and is the single spacing/quota choke point every other
    # Gemini call in the codebase already routes through.
    response = gemini_pipe.generate_content_tracked(
        client, gemini_pipe.GEMINI_MODEL, contents=[prompt], config=config,
    )
    return response.text or ""


def _generate_local(prompt: str, *, json_mode: bool) -> str:
    from app import local_llm

    return local_llm.generate(prompt, json_mode=json_mode)


def generate_json(task: str, prompt: str, schema_cls):
    """generate_text + validate against `schema_cls`, with ONE retry using a
    stricter re-prompt if the first attempt doesn't parse/validate -- the same
    defensive shape every existing Gemini-only call site already used
    (attempt, catch JSONDecodeError/ValidationError, retry once), now shared
    across every task routed through here instead of duplicated per call site.

    Raises the last validation error if both attempts fail. A provider-level
    error (OllamaUnavailable, a Gemini quota/network error) is NOT retried and
    propagates immediately, unchanged -- retrying a down or rate-limited
    provider with a different prompt wouldn't help, and callers need to see
    the real error to decide whether to stop their whole batch (a quota hit)
    or just skip this one row."""
    from pydantic import ValidationError

    current_prompt = prompt
    last_exc: Optional[Exception] = None
    for attempt in range(2):
        raw = generate_text(task, current_prompt, response_schema=schema_cls)
        try:
            return schema_cls.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            last_exc = exc
            logger.warning(
                "llm_router.generate_json: schema validation failed for task=%r "
                "(attempt %d/2): %s", task, attempt + 1, exc,
            )
            current_prompt = prompt + STRICT_JSON_RETRY_SUFFIX
    raise last_exc  # noqa: RSE102 - re-raising the last captured validation failure
