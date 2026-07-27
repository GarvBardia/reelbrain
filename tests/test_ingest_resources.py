"""Tests for scripts/ingest_resources.py — mocked fetch/Gemini only, never a
live network call (conftest blocks httpx.get/post as defense-in-depth; every
test here injects its own fake instead)."""
import json

import pytest

from app.models import ResourceExtraction
from scripts import ingest_resources as ir


class _FakeResponse:
    def __init__(self, status_code=200, text="", content=b"", headers=None, url=""):
        self.status_code = status_code
        self.text = text
        self.content = content
        self.headers = headers or {}
        self.url = url


# --- classify_resource_url ------------------------------------------------------

@pytest.mark.parametrize("url,expected", [
    ("https://github.com/oso95/scroll-world", "github_repo"),
    ("https://docs.google.com/document/d/abc123/mobilebasic", "google_doc"),
    ("https://drive.google.com/file/d/abc123/view", "google_drive_file"),
    ("https://example.com/guide.pdf", "pdf"),
    ("https://getdesign.md/", "web_article"),
    ("https://vibha-ramprakash.github.io/seo-geo-tracker/", "web_article"),
])
def test_classify_resource_url(url, expected):
    assert ir.classify_resource_url(url) == expected


# --- fetch_github_readme --------------------------------------------------------

def test_fetch_github_readme_success(monkeypatch):
    def _fake_get(url, headers=None, timeout=None, follow_redirects=None):
        assert "api.github.com/repos/oso95/scroll-world/readme" in url
        return _FakeResponse(200, text="# Scroll World\n\nAn open-source Claude skill...")

    import httpx
    monkeypatch.setattr(httpx, "get", _fake_get)

    content, error = ir.fetch_github_readme("https://github.com/oso95/scroll-world")
    assert error is None
    assert "Scroll World" in content


def test_fetch_github_readme_not_found(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(404))

    content, error = ir.fetch_github_readme("https://github.com/x/y")
    assert content is None
    assert "no README" in error


def test_fetch_github_readme_rate_limited(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(403))

    content, error = ir.fetch_github_readme("https://github.com/x/y")
    assert content is None
    assert "rate-limited" in error


def test_fetch_github_readme_bad_url():
    content, error = ir.fetch_github_readme("https://github.com/")
    assert content is None
    assert "couldn't parse" in error


# --- fetch_google_doc ------------------------------------------------------------

def test_fetch_google_doc_success(monkeypatch):
    html = (
        "<html><body><h1>Guide</h1><p>Do the thing step by step in careful, "
        "thorough detail right here so that this paragraph clears the minimum "
        "word count threshold used to detect near-empty pages in the fetcher.</p>"
        "</body></html>"
    )

    def _fake_get(url, headers=None, timeout=None, follow_redirects=None):
        assert url.endswith("/mobilebasic")
        return _FakeResponse(200, text=html, url=url)

    import httpx
    monkeypatch.setattr(httpx, "get", _fake_get)

    content, error = ir.fetch_google_doc("https://docs.google.com/document/d/abc/mobilebasic")
    assert error is None
    assert "Guide" in content
    assert "Do the thing" in content


def test_fetch_google_doc_requires_sign_in(monkeypatch):
    def _fake_get(url, headers=None, timeout=None, follow_redirects=None):
        return _FakeResponse(200, text="<html><body>Sign in - Google Accounts</body></html>", url=url)

    import httpx
    monkeypatch.setattr(httpx, "get", _fake_get)

    content, error = ir.fetch_google_doc("https://docs.google.com/document/d/abc/mobilebasic")
    assert content is None
    assert "sign-in" in error


def test_fetch_google_doc_thin_content(monkeypatch):
    def _fake_get(url, headers=None, timeout=None, follow_redirects=None):
        return _FakeResponse(200, text="<html><body>hi</body></html>", url=url)

    import httpx
    monkeypatch.setattr(httpx, "get", _fake_get)

    content, error = ir.fetch_google_doc("https://docs.google.com/document/d/abc/mobilebasic")
    assert content is None
    assert "almost no extractable text" in error


# --- fetch_drive_file ------------------------------------------------------------

def test_fetch_drive_file_html_wall(monkeypatch):
    def _fake_get(url, headers=None, timeout=None, follow_redirects=None):
        return _FakeResponse(200, text="<html>sign in</html>", headers={"content-type": "text/html"})

    import httpx
    monkeypatch.setattr(httpx, "get", _fake_get)

    content, error = ir.fetch_drive_file("https://drive.google.com/file/d/XYZ123/view")
    assert content is None
    assert "requires login" in error


def test_fetch_drive_file_pdf_success(monkeypatch):
    def _fake_get(url, headers=None, timeout=None, follow_redirects=None):
        return _FakeResponse(200, content=b"%PDF-fake-bytes", headers={"content-type": "application/pdf"})

    import httpx
    monkeypatch.setattr(httpx, "get", _fake_get)
    monkeypatch.setattr(ir, "_extract_pdf_text", lambda b: ("Real extracted PDF text here, plenty of words.", None))

    content, error = ir.fetch_drive_file("https://drive.google.com/file/d/XYZ123/view")
    assert error is None
    assert "Real extracted PDF text" in content


def test_fetch_drive_file_bad_url():
    content, error = ir.fetch_drive_file("https://drive.google.com/drive/folders/abc")
    assert content is None
    assert "couldn't parse" in error


# --- _extract_pdf_text -----------------------------------------------------------

def test_extract_pdf_text_corrupt_bytes():
    content, error = ir._extract_pdf_text(b"not a real pdf at all")
    assert content is None
    assert "couldn't parse as PDF" in error


def test_extract_pdf_text_success(monkeypatch):
    class _FakePage:
        def extract_text(self):
            return "This page has plenty of real extractable words in it for the test."

    class _FakeReader:
        def __init__(self, _stream):
            self.is_encrypted = False
            self.pages = [_FakePage(), _FakePage()]

    import pypdf
    monkeypatch.setattr(pypdf, "PdfReader", _FakeReader)

    content, error = ir._extract_pdf_text(b"irrelevant-because-PdfReader-is-mocked")
    assert error is None
    assert "plenty of real extractable words" in content


# --- fetch_web_article -----------------------------------------------------------

def test_fetch_web_article_success(monkeypatch):
    html = (
        "<html><body><article><h1>Title</h1><p>Some real article body content "
        "goes right here, with enough words in this paragraph to clear the "
        "minimum word count threshold the fetcher uses to reject near-empty pages.</p>"
        "</article></body></html>"
    )

    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(200, text=html))

    content, error = ir.fetch_web_article("https://example.com/post")
    assert error is None
    assert "Some real article body content" in content


def test_fetch_web_article_http_error(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(404))

    content, error = ir.fetch_web_article("https://example.com/gone")
    assert content is None
    assert "404" in error


def test_fetch_web_article_connection_error(monkeypatch):
    import httpx

    def _boom(*a, **kw):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(httpx, "get", _boom)

    content, error = ir.fetch_web_article("https://example.com/down")
    assert content is None
    assert "connection refused" in error


# --- build_resource_note / resource_note_path -------------------------------------

def test_resource_note_path_shape():
    path = ir.resource_note_path(__import__("pathlib").Path("/vault"), "AB12cd", "A Great Guide!")
    assert str(path).replace("\\", "/") == "/vault/resources/AB12cd-a-great-guide.md"


def test_build_resource_note_shape():
    entry = {"shortcode": "AB12cd", "reel_title": "A Great Guide"}
    extraction = ResourceExtraction(
        summary="This guide explains a five-step workflow for X.",
        key_takeaways=["Step one is Y", "Step two is Z"],
        topic_tags=["ai-tools", "developer-tools"],
        resource_kind="github_repo",
    )
    note = ir.build_resource_note(entry, "2026-07-20-AB12cd", extraction, "https://github.com/x/y")

    assert "source_shortcode: AB12cd" in note
    assert 'source_reel: "[[reels/2026-07-20-AB12cd]]"' in note
    assert "resource_kind: github_repo" in note
    assert 'topics_plain: ai-tools, developer-tools' in note
    assert "## Summary" in note
    assert "This guide explains a five-step workflow for X." in note
    assert "## Key takeaways" in note
    assert "- Step one is Y" in note
    assert "<https://github.com/x/y>" in note


def test_build_resource_note_no_reel_stem_omits_source_reel():
    entry = {"shortcode": "AB12cd", "reel_title": "A Great Guide"}
    extraction = ResourceExtraction(summary="Summary.", resource_kind="web_article")
    note = ir.build_resource_note(entry, None, extraction, "https://example.com")
    assert "source_reel" not in note


# --- run_ingest orchestration ------------------------------------------------------

def _entry(shortcode="SC1", url="https://example.com/x"):
    return {"shortcode": shortcode, "reel_title": "Some Reel", "resource_url": url, "topics": []}


def test_run_ingest_writes_note_on_success(tmp_path):
    entries = [_entry()]
    extraction = ResourceExtraction(summary="Good summary.", topic_tags=["ai"], resource_kind="web_article")

    result = ir.run_ingest(
        entries, tmp_path, tmp_path / "progress.json",
        fetch_fn=lambda url, kind: ("plenty of real fetched content here", None),
        extract_fn=lambda content, kind, title, taxonomy: extraction,
        sleep_fn=lambda s: None, print_fn=lambda *a, **k: None,
    )

    assert result["written"] == ["SC1"]
    note_path = tmp_path / "resources" / "SC1-some-reel.md"
    assert note_path.exists()
    assert "Good summary." in note_path.read_text(encoding="utf-8")


def test_run_ingest_unreadable_not_written(tmp_path):
    entries = [_entry()]
    result = ir.run_ingest(
        entries, tmp_path, tmp_path / "progress.json",
        fetch_fn=lambda url, kind: (None, "requires login"),
        extract_fn=lambda *a, **k: pytest.fail("must not call Gemini when fetch failed"),
        sleep_fn=lambda s: None, print_fn=lambda *a, **k: None,
    )
    assert result["unreadable"] == ["SC1"]
    assert result["written"] == []
    assert not (tmp_path / "resources").exists()

    progress = json.loads((tmp_path / "progress.json").read_text(encoding="utf-8"))
    assert progress["SC1"]["status"] == "unreadable"
    assert progress["SC1"]["error"] == "requires login"


def test_run_ingest_degraded_extraction_not_written(tmp_path):
    entries = [_entry()]
    result = ir.run_ingest(
        entries, tmp_path, tmp_path / "progress.json",
        fetch_fn=lambda url, kind: ("plenty of real content here for the test", None),
        extract_fn=lambda *a, **k: None,  # Gemini degraded
        sleep_fn=lambda s: None, print_fn=lambda *a, **k: None,
    )
    assert result["degraded"] == ["SC1"]
    assert result["written"] == []
    progress = json.loads((tmp_path / "progress.json").read_text(encoding="utf-8"))
    assert progress["SC1"]["status"] == "degraded"


def test_run_ingest_dry_run_never_writes_files(tmp_path):
    entries = [_entry()]
    extraction = ResourceExtraction(summary="Dry run summary.", resource_kind="web_article")
    result = ir.run_ingest(
        entries, tmp_path, tmp_path / "progress.json", dry_run=True,
        fetch_fn=lambda url, kind: ("plenty of real content here for the test", None),
        extract_fn=lambda *a, **k: extraction,
        sleep_fn=lambda s: None, print_fn=lambda *a, **k: None,
    )
    assert result["written"] == ["SC1"]
    assert not (tmp_path / "resources").exists()
    assert not (tmp_path / "progress.json").exists()  # dry-run never persists progress either


def test_run_ingest_dry_run_makes_no_fetch_or_gemini_call(tmp_path):
    """A dry run must be genuinely free -- safe to run anytime during a
    quota-constrained countdown, not just cheaper. It reports what WOULD be
    attempted without any network fetch or Gemini call."""
    fetch_calls = []
    extract_calls = []
    result = ir.run_ingest(
        [_entry()], tmp_path, tmp_path / "progress.json", dry_run=True,
        fetch_fn=lambda url, kind: (fetch_calls.append(1) or ("x", None)),
        extract_fn=lambda *a, **k: (extract_calls.append(1) or None),
        sleep_fn=lambda s: None, print_fn=lambda *a, **k: None,
    )
    assert fetch_calls == []
    assert extract_calls == []
    assert result["written"] == ["SC1"]


def test_run_ingest_skips_already_written(tmp_path):
    progress_file = tmp_path / "progress.json"
    progress_file.write_text(json.dumps({"SC1": {"status": "written"}}), encoding="utf-8")

    calls = []
    result = ir.run_ingest(
        [_entry()], tmp_path, progress_file,
        fetch_fn=lambda url, kind: calls.append(1) or ("x", None),
        extract_fn=lambda *a, **k: None,
        sleep_fn=lambda s: None, print_fn=lambda *a, **k: None,
    )
    assert result["skipped_done"] == ["SC1"]
    assert calls == []  # never even fetched


def test_run_ingest_retries_unreadable_and_degraded_on_next_run(tmp_path):
    """Unlike 'written', unreadable/degraded rows are NOT terminal -- they
    should be retried on the next run rather than skipped forever."""
    progress_file = tmp_path / "progress.json"
    progress_file.write_text(
        json.dumps({"SC1": {"status": "unreadable"}, "SC2": {"status": "degraded"}}), encoding="utf-8"
    )
    calls = []
    result = ir.run_ingest(
        [_entry("SC1"), _entry("SC2")], tmp_path, progress_file,
        fetch_fn=lambda url, kind: calls.append(url) or (None, "still broken"),
        extract_fn=lambda *a, **k: None,
        sleep_fn=lambda s: None, print_fn=lambda *a, **k: None,
    )
    assert len(calls) == 2  # both retried, not skipped
    assert set(result["unreadable"]) == {"SC1", "SC2"}
