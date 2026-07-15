"""Env-var secrets must be stripped at read time so a value pasted into a hosting
dashboard with a trailing newline (the Render bug) doesn't break the API call.

Each module reads its credentials into constants at import time, so to test the
stripping we set the env var and reimport the module with importlib.reload.
"""
import importlib

import app.gemini_pipe as gemini_pipe
import app.main as main
import app.notion_writer as notion_writer


def test_notion_token_and_ids_stripped(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "secret_token_value\n")
    monkeypatch.setenv("NOTION_DB_ID", "  db-id-123\n")
    monkeypatch.setenv("NOTION_CREATORS_DB_ID", "creators-id\r\n")
    reloaded = importlib.reload(notion_writer)
    try:
        assert reloaded.NOTION_TOKEN == "secret_token_value"
        assert reloaded.NOTION_DB_ID == "db-id-123"
        assert reloaded.NOTION_CREATORS_DB_ID == "creators-id"
    finally:
        # restore module state so the trailing-whitespace values don't leak into
        # other tests that import notion_writer at its normal (unset) env.
        monkeypatch.delenv("NOTION_TOKEN", raising=False)
        monkeypatch.delenv("NOTION_DB_ID", raising=False)
        monkeypatch.delenv("NOTION_CREATORS_DB_ID", raising=False)
        importlib.reload(notion_writer)


def test_capture_secret_stripped_and_matches_request(monkeypatch):
    monkeypatch.setenv("CAPTURE_SECRET", "topsecret\n")
    reloaded = importlib.reload(main)
    try:
        assert reloaded.CAPTURE_SECRET == "topsecret"
        # a clean inbound secret matches the stripped stored value...
        reloaded._check_secret("topsecret")  # no exception == pass
        # ...and so does an inbound secret that itself carries whitespace
        reloaded._check_secret("topsecret\n")
    finally:
        monkeypatch.delenv("CAPTURE_SECRET", raising=False)
        importlib.reload(main)


def test_gemini_api_key_stripped(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-fake-key\n")
    reloaded = importlib.reload(gemini_pipe)
    try:
        assert reloaded.GEMINI_API_KEY == "AIza-fake-key"
    finally:
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        importlib.reload(gemini_pipe)
