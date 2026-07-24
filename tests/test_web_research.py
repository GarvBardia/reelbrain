"""app/web_research.py + the run_research_context web-fetch fallback. All mocked."""
import pytest

from app import gemini_pipe, web_research
from app.models import ResearchContextItem


# --- DDG parsing ----------------------------------------------------------------

DDG_HTML = '''
<div class="result">
<a rel="nofollow" class="result__a"
 href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fgithub.com%2Fmendableai%2Ffirecrawl&amp;rut=abc">
Firecrawl</a></div>
'''


def test_ddg_top_result_unwraps_redirect(monkeypatch):
    import httpx

    class _Resp:
        status_code = 200
        text = DDG_HTML
        def raise_for_status(self): pass

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp())
    monkeypatch.setattr(web_research, "_enforce_ddg_spacing", lambda **k: None)
    assert web_research.ddg_top_result_url("firecrawl") == "https://github.com/mendableai/firecrawl"


def test_ddg_top_result_none_on_failure(monkeypatch):
    import httpx

    def _boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(httpx, "get", _boom)
    monkeypatch.setattr(web_research, "_enforce_ddg_spacing", lambda **k: None)
    assert web_research.ddg_top_result_url("anything") is None


def test_strip_tags_removes_scripts_and_collapses_whitespace():
    html = "<html><script>var x=1;</script><body><h1>Title</h1>\n<p>Real &amp; text</p></body></html>"
    assert web_research._strip_tags(html) == "Title Real & text"


# --- fetch_context_material routing ----------------------------------------------

def test_material_prefers_github_readme_for_repo_results(monkeypatch):
    monkeypatch.setattr(web_research, "ddg_top_result_url",
                        lambda q: "https://github.com/owner/repo")
    monkeypatch.setattr(web_research, "fetch_github_readme",
                        lambda o, r: "# Repo\nA real readme " + "words " * 30)
    material, url = web_research.fetch_context_material("some tool")
    assert material.startswith("# Repo")
    assert url == "https://github.com/owner/repo"


def test_material_falls_back_to_page_text(monkeypatch):
    monkeypatch.setattr(web_research, "ddg_top_result_url", lambda q: "https://example.com/x")
    monkeypatch.setattr(web_research, "fetch_page_text", lambda u: "real page text " * 20)
    material, url = web_research.fetch_context_material("thing")
    assert "real page text" in material
    assert url == "https://example.com/x"


def test_material_rejects_near_empty_pages(monkeypatch):
    monkeypatch.setattr(web_research, "ddg_top_result_url", lambda q: "https://example.com/x")
    monkeypatch.setattr(web_research, "fetch_page_text", lambda u: "too thin")
    assert web_research.fetch_context_material("thing") == (None, None)


def test_material_none_when_no_search_result(monkeypatch):
    monkeypatch.setattr(web_research, "ddg_top_result_url", lambda q: None)
    assert web_research.fetch_context_material("thing") == (None, None)


# --- run_research_context fallback wiring ----------------------------------------

def _blocked_grounded_call(entity):
    raise RuntimeError("429 RESOURCE_EXHAUSTED quota")


def test_fallback_used_when_grounding_429s(monkeypatch):
    monkeypatch.setattr(gemini_pipe, "_call_gemini_research", _blocked_grounded_call)
    monkeypatch.setattr(
        web_research, "fetch_context_material",
        lambda e: ("fetched material " * 10, "https://example.com/src"),
    )
    monkeypatch.setattr(
        gemini_pipe, "_call_gemini_webfetch_context",
        lambda entity, material, url: f"{entity} is a real tool per the fetched page.",
    )
    results = gemini_pipe.run_research_context(["Firecrawl", "uv"])
    assert len(results) == 2
    assert all(r.source == "web-fetch" for r in results)
    assert results[0].context == "Firecrawl is a real tool per the fetched page."


def test_fallback_honest_not_found_when_nothing_fetched(monkeypatch):
    monkeypatch.setattr(gemini_pipe, "_call_gemini_research", _blocked_grounded_call)
    monkeypatch.setattr(web_research, "fetch_context_material", lambda e: (None, None))
    called = []
    monkeypatch.setattr(gemini_pipe, "_call_gemini_webfetch_context",
                        lambda *a: called.append(a) or "x")
    results = gemini_pipe.run_research_context(["GhostEntity9000"])
    assert results == [ResearchContextItem(
        topic="GhostEntity9000", context=gemini_pipe.NOT_FOUND_VIA_SEARCH, source="web-fetch")]
    assert called == []  # no material -> Gemini never asked to guess


def test_grounding_429_stops_further_grounded_attempts(monkeypatch):
    grounded_calls = []

    def grounded(entity):
        grounded_calls.append(entity)
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    monkeypatch.setattr(gemini_pipe, "_call_gemini_research", grounded)
    monkeypatch.setattr(web_research, "fetch_context_material", lambda e: (None, None))
    gemini_pipe.run_research_context(["a", "b", "c"])
    assert grounded_calls == ["a"]  # 2nd/3rd entity skip straight to fallback


def test_grounded_success_still_marks_search_grounding(monkeypatch):
    class _Chunk: pass

    class _Meta: grounding_chunks = [_Chunk()]

    class _Cand: grounding_metadata = _Meta()

    class _Resp:
        candidates = [_Cand()]
        text = "A grounded answer."

    monkeypatch.setattr(gemini_pipe, "_call_gemini_research", lambda e: _Resp())
    results = gemini_pipe.run_research_context(["known-tool"])
    assert results[0].source == "search-grounding"
    assert results[0].context == "A grounded answer."


def test_research_toggle_renders_source_marker():
    from app import notion_writer
    from app.models import Extraction

    extraction = Extraction(
        main_point="x",
        research_context=[ResearchContextItem(topic="uv", context="A fast installer.", source="web-fetch")],
    )
    blocks = notion_writer._build_children(extraction, "cap")
    toggle = next(b for b in blocks if b["type"] == "toggle"
                  and b["toggle"]["rich_text"][0]["text"]["content"] == "Research Context")
    para = toggle["toggle"]["children"][0]["paragraph"]["rich_text"][0]["text"]["content"]
    assert para == "uv [web-fetch]: A fast installer."
