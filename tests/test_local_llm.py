"""app/local_llm.py — the Ollama client (2026-08-16, PROGRESS.md: permanent
local-LLM fix for the Gemini free-tier quota bottleneck). All mocked at the
httpx boundary -- no real Ollama process required to run these."""
import httpx
import pytest

from app import local_llm


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad status", request=None, response=self)


def test_generate_returns_response_text(monkeypatch):
    calls = []

    def _fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        return _FakeResponse({"response": "hello from ollama"})

    monkeypatch.setattr(httpx, "post", _fake_post)

    result = local_llm.generate("say hi")

    assert result == "hello from ollama"
    assert calls[0][0] == f"{local_llm.OLLAMA_HOST}/api/generate"
    assert calls[0][1]["model"] == local_llm.LOCAL_LLM_MODEL
    assert calls[0][1]["prompt"] == "say hi"
    assert calls[0][1]["stream"] is False
    assert "format" not in calls[0][1]
    assert calls[0][2] == local_llm.LOCAL_LLM_TIMEOUT_SECONDS


def test_generate_json_mode_sets_format_field(monkeypatch):
    captured = {}

    def _fake_post(url, json, timeout):
        captured.update(json)
        return _FakeResponse({"response": "{}"})

    monkeypatch.setattr(httpx, "post", _fake_post)

    local_llm.generate("give me json", json_mode=True)

    assert captured["format"] == "json"


def test_generate_raises_ollama_unavailable_on_connect_error(monkeypatch):
    def _fake_post(url, json, timeout):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", _fake_post)

    with pytest.raises(local_llm.OllamaUnavailable):
        local_llm.generate("say hi")


def test_generate_propagates_http_status_error_without_masking_as_unavailable(monkeypatch):
    """A reachable-but-erroring Ollama (e.g. bad request, model not pulled) is
    an ordinary failure -- must NOT be reported as OllamaUnavailable, since a
    caller uses that specific exception to decide whether Ollama itself is
    down (skip the whole local run) vs. this one call just failed (skip this
    row, keep going)."""
    def _fake_post(url, json, timeout):
        return _FakeResponse({"error": "model not found"}, status_code=404)

    monkeypatch.setattr(httpx, "post", _fake_post)

    with pytest.raises(httpx.HTTPStatusError):
        local_llm.generate("say hi")


def test_generate_propagates_timeout_without_masking_as_unavailable(monkeypatch):
    def _fake_post(url, json, timeout):
        raise httpx.TimeoutException("took too long")

    monkeypatch.setattr(httpx, "post", _fake_post)

    with pytest.raises(httpx.TimeoutException):
        local_llm.generate("say hi")
