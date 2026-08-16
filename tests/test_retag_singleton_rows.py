"""scripts/retag_singleton_rows.py — the taxonomy-collapse repair pass (2026-08-09
incident, see PROGRESS.md). Selection logic + the resumable batch loop, mocked."""
from scripts import retag_singleton_rows as rsr


def _page(shortcode, title, topics, content_type="insight", entities=None):
    return {
        "id": f"pg-{shortcode}",
        "url": f"https://notion.so/pg-{shortcode}",
        "properties": {
            "Shortcode": {"rich_text": [{"plain_text": shortcode}]},
            "Title": {"title": [{"plain_text": title}]},
            "Topics": {"multi_select": [{"name": t} for t in topics]},
            "Priority": {"select": {"name": "Medium"}},
            "Value score": {"select": {"name": "3"}},
            "Status": {"select": {"name": "📥 Inbox"}},
            "Named entities": {"multi_select": [{"name": e} for e in (entities or [])]},
            "Content type": {"select": {"name": content_type}},
            "Reel URL": {"url": f"https://www.instagram.com/reel/{shortcode}/"},
        },
    }


# --- find_singleton_rows ------------------------------------------------------------

def test_finds_rows_whose_tag_combination_is_unique():
    pages = [
        _page("A1", "one", ["fitness", "habits"]),
        _page("A2", "two", ["fitness", "habits"]),   # shares A1's combo -- not a singleton
        _page("B1", "three", ["zoey", "startup-analysis", "elu"]),  # unique -- singleton
    ]
    rows = rsr.find_singleton_rows(pages)
    assert [r["shortcode"] for r in rows] == ["B1"]


def test_excludes_marker_only_rows():
    pages = [
        _page("M1", "one", ["uncategorized"]),
        _page("M2", "two", ["near-duplicate"]),
        _page("M3", "three", ["pending-extraction"]),
    ]
    assert rsr.find_singleton_rows(pages) == []


def test_selection_is_merge_and_plural_aware():
    """A row tagged only 'claude' and a row tagged only 'claude-ai' are the SAME
    canonical combination -- neither should count as a singleton just because
    the raw spellings differ. Same for singular/plural drift."""
    pages = [
        _page("C1", "one", ["claude"]),
        _page("C2", "two", ["claude-ai"]),
        _page("S1", "three", ["startup"]),
        _page("S2", "four", ["startups"]),
    ]
    assert rsr.find_singleton_rows(pages) == []


def test_singleton_row_carries_the_fields_the_retag_prompt_needs():
    pages = [_page("D1", "A real main point", ["zoey", "jarvis"],
                    content_type="resource_drop", entities=["Zoey", "Jarvis"])]
    rows = rsr.find_singleton_rows(pages)
    assert len(rows) == 1
    row = rows[0]
    assert row["main_point"] == "A real main point"
    assert row["current_topics"] == ["zoey", "jarvis"]
    assert row["named_entities"] == ["Zoey", "Jarvis"]
    assert row["content_type"] == "resource_drop"
    assert row["page_id"] == "pg-D1"


def test_rows_without_a_shortcode_are_skipped():
    page = _page("", "one", ["fitness"])
    assert rsr.find_singleton_rows([page]) == []


# --- run_retag ------------------------------------------------------------------

def _rows(*shortcodes):
    return [{"shortcode": s, "page_id": f"pg-{s}", "main_point": f"point {s}",
             "named_entities": [], "content_type": "insight",
             "current_topics": ["old-tag"]} for s in shortcodes]


def test_run_retag_dry_run_calls_nothing(tmp_path):
    called = []
    summary = rsr.run_retag(
        _rows("A1"), ["fitness"], str(tmp_path / "p.json"), dry_run=True,
        retag_fn=lambda *a: called.append(a) or ["fitness"],
        body_fn=lambda pid: [], write_fn=lambda pid, t: (_ for _ in ()).throw(AssertionError("must not write")),
        print_fn=lambda m: None,
    )
    assert called == []
    assert summary["written"] == 0


def test_run_retag_writes_and_resumes(tmp_path):
    progress_file = str(tmp_path / "p.json")
    written = []
    rsr.run_retag(
        _rows("A1", "B2"), ["fitness"], progress_file,
        retag_fn=lambda mp, sp, ne, ct, tax: ["fitness", "habits"],
        body_fn=lambda pid: [], write_fn=lambda pid, t: written.append((pid, t)),
        print_fn=lambda m: None,
    )
    assert len(written) == 2
    assert written[0] == ("pg-A1", ["fitness", "habits"])

    written.clear()
    summary = rsr.run_retag(
        _rows("A1", "B2"), ["fitness"], progress_file,
        retag_fn=lambda mp, sp, ne, ct, tax: (_ for _ in ()).throw(AssertionError("must not be called")),
        body_fn=lambda pid: [], write_fn=lambda pid, t: written.append(t),
        print_fn=lambda m: None,
    )
    assert written == []
    assert summary["skipped"] == 2


def test_run_retag_degraded_result_is_not_written(tmp_path):
    written = []
    summary = rsr.run_retag(
        _rows("A1"), ["fitness"], str(tmp_path / "p.json"),
        retag_fn=lambda mp, sp, ne, ct, tax: None,
        body_fn=lambda pid: [], write_fn=lambda pid, t: written.append(t),
        print_fn=lambda m: None,
    )
    assert written == []
    assert summary["errors"] == 1
    assert summary["written"] == 0


def test_run_retag_quota_error_halts_cleanly_and_is_resumable(tmp_path):
    progress_file = str(tmp_path / "p.json")

    def _boom(mp, sp, ne, ct, tax):
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    attempted = []
    summary = rsr.run_retag(
        _rows("Q1", "NEVER1"), ["fitness"], progress_file,
        retag_fn=lambda mp, sp, ne, ct, tax: attempted.append(mp) or _boom(mp, sp, ne, ct, tax),
        body_fn=lambda pid: [], write_fn=lambda pid, t: None,
        print_fn=lambda m: None,
    )
    assert len(attempted) == 1
    assert summary["quota_stopped"] is True
    assert summary["written"] == 0


def test_run_retag_ollama_unavailable_halts_cleanly_never_falls_back_to_gemini(tmp_path):
    """Phase 5 hard boundary: Ollama down must stop the batch cleanly (every
    remaining row would fail identically) and must never silently retry via
    Gemini -- proven by asserting the Gemini call site is never touched."""
    from app import local_llm

    def _boom(mp, sp, ne, ct, tax):
        raise local_llm.OllamaUnavailable("ollama not running")

    attempted = []
    summary = rsr.run_retag(
        _rows("O1", "NEVER1"), ["fitness"], str(tmp_path / "p.json"),
        retag_fn=lambda mp, sp, ne, ct, tax: attempted.append(mp) or _boom(mp, sp, ne, ct, tax),
        body_fn=lambda pid: [], write_fn=lambda pid, t: None,
        print_fn=lambda m: None,
    )

    assert len(attempted) == 1  # NEVER1 was never attempted
    assert summary["ollama_stopped"] is True
    assert summary["quota_stopped"] is False
    assert summary["written"] == 0


def test_run_retag_write_failure_is_retryable_not_fatal(tmp_path):
    def _boom_write(pid, t):
        raise RuntimeError("notion 500")

    summary = rsr.run_retag(
        _rows("A1"), ["fitness"], str(tmp_path / "p.json"),
        retag_fn=lambda mp, sp, ne, ct, tax: ["fitness"],
        body_fn=lambda pid: [], write_fn=_boom_write,
        print_fn=lambda m: None,
    )
    assert summary["errors"] == 1
    assert summary["written"] == 0
    assert summary["quota_stopped"] is False
