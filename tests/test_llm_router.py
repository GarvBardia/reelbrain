"""app/llm_router.py — the provider-routing choke point (2026-08-16,
PROGRESS.md: local Ollama as the permanent fix for the Gemini free-tier quota
bottleneck). All mocked -- no real Gemini or Ollama calls."""
import pytest
from pydantic import BaseModel

from app import llm_router, local_llm


class _Tags(BaseModel):
    topic_tags: list[str]


# --- routing table ---------------------------------------------------------------

def test_local_routed_tasks():
    for task in ("plain_summary_backfill", "topic_retag",
                 "notion_deep_clean_tagging", "research_context_web_fetch"):
        assert llm_router.provider_for(task) == llm_router.LOCAL


def test_gemini_routed_tasks():
    for task in ("full_extraction", "caption_only_extraction", "carousel_extraction",
                 "recover_placeholders", "research_context_grounded",
                 "suggested_action_backfill"):
        assert llm_router.provider_for(task) == llm_router.GEMINI


def test_suggested_action_stays_on_gemini_despite_being_text_only():
    """PROGRESS.md 2026-08-16 Phase 4: the one exception among the five
    text-only candidate tasks -- a live 3-row comparison found local
    (llama3.1:8b) output materially worse for this specific task."""
    assert llm_router.provider_for("suggested_action_backfill") == llm_router.GEMINI


def test_unknown_task_defaults_to_gemini():
    """The safer failure mode if a call site is ever wired up without adding
    a TASK_PROVIDERS entry: a quota-tracked Gemini call, not a silently
    untracked local one."""
    assert llm_router.provider_for("some-task-nobody-registered") == llm_router.GEMINI


# --- generate_text routing --------------------------------------------------------

def test_generate_text_local_task_calls_local_never_gemini(monkeypatch):
    gemini_called = []
    monkeypatch.setattr(llm_router, "_generate_gemini", lambda *a, **k: gemini_called.append(1) or "x")
    monkeypatch.setattr(llm_router, "_generate_local", lambda prompt, json_mode: "local reply")

    result = llm_router.generate_text("topic_retag", "hello")

    assert result == "local reply"
    assert gemini_called == []


def test_generate_text_gemini_task_calls_gemini_never_local(monkeypatch):
    local_called = []
    monkeypatch.setattr(llm_router, "_generate_local", lambda *a, **k: local_called.append(1) or "x")
    monkeypatch.setattr(llm_router, "_generate_gemini", lambda prompt, response_schema=None: "gemini reply")

    result = llm_router.generate_text("full_extraction", "hello")

    assert result == "gemini reply"
    assert local_called == []


def test_generate_text_local_path_never_touches_gemini_quota(monkeypatch, tmp_path):
    """The whole point of routing to local: it must not cost a Gemini call.
    Proven by never touching gemini_pipe.generate_content_tracked at all."""
    from app import gemini_pipe

    def _must_not_be_called(*a, **k):
        raise AssertionError("local-routed task must never call generate_content_tracked")

    monkeypatch.setattr(gemini_pipe, "generate_content_tracked", _must_not_be_called)
    monkeypatch.setattr(local_llm, "generate", lambda prompt, json_mode=False: "ok")

    result = llm_router.generate_text("plain_summary_backfill", "hello")
    assert result == "ok"


# --- generate_json validate + retry-with-stricter-prompt --------------------------

def test_generate_json_succeeds_first_try(monkeypatch):
    monkeypatch.setattr(llm_router, "generate_text", lambda task, prompt, **k: '{"topic_tags": ["fitness"]}')

    result = llm_router.generate_json("topic_retag", "prompt", _Tags)
    assert result.topic_tags == ["fitness"]


def test_generate_json_retries_once_with_stricter_prompt_then_succeeds(monkeypatch):
    prompts_seen = []

    def _fake_generate_text(task, prompt, **kwargs):
        prompts_seen.append(prompt)
        if len(prompts_seen) == 1:
            return "not valid json"
        return '{"topic_tags": ["fitness"]}'

    monkeypatch.setattr(llm_router, "generate_text", _fake_generate_text)

    result = llm_router.generate_json("topic_retag", "base prompt", _Tags)

    assert result.topic_tags == ["fitness"]
    assert len(prompts_seen) == 2
    assert prompts_seen[0] == "base prompt"
    assert llm_router.STRICT_JSON_RETRY_SUFFIX in prompts_seen[1]
    assert prompts_seen[1].startswith("base prompt")


def test_generate_json_raises_after_two_failures(monkeypatch):
    monkeypatch.setattr(llm_router, "generate_text", lambda task, prompt, **k: "still not json")

    with pytest.raises(Exception):
        llm_router.generate_json("topic_retag", "prompt", _Tags)


def test_generate_json_does_not_retry_a_provider_error(monkeypatch):
    """OllamaUnavailable (or a Gemini quota/network error) must propagate
    immediately, not get retried with a stricter prompt -- retrying a down
    provider can't help, and the caller needs the real error to decide
    whether to stop its whole batch."""
    calls = []

    def _boom(task, prompt, **kwargs):
        calls.append(prompt)
        raise local_llm.OllamaUnavailable("ollama not running")

    monkeypatch.setattr(llm_router, "generate_text", _boom)

    with pytest.raises(local_llm.OllamaUnavailable):
        llm_router.generate_json("topic_retag", "prompt", _Tags)

    assert len(calls) == 1  # never retried
