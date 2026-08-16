"""Phase 2: suggested_action field — prompts, Notion property, Obsidian "## Do"
line, and the backfill script. All mocked."""
from pathlib import Path

import pytest

from app import notion_writer, obsidian_sync
from app.models import Extraction, ReelData
from scripts import backfill_suggested_action as bsa

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


# --- prompts --------------------------------------------------------------------

@pytest.mark.parametrize("name", ["extraction.md", "extraction_caption_only.md"])
def test_prompts_carry_suggested_action_and_value_anchors(name):
    text = (PROMPTS_DIR / name).read_text(encoding="utf-8")
    assert "`suggested_action`" in text
    assert "none — informational" in text
    assert "complete, actionable system" in text          # value_score 5 anchor
    assert "thin comment-bait" in text                    # value_score 2 anchor
    # main_point specificity. Phase C replaced the earlier "must name the
    # specific tools" wording with a stronger stand-alone requirement that
    # still mandates keeping the tool names (and adds a jargon-gloss rule).
    assert "STANDS ALONE" in text
    assert "Keep the specific tool names" in text


# --- Notion property write + read-back ------------------------------------------

def _reel():
    return ReelData(shortcode="SA1", permalink="https://www.instagram.com/reel/SA1/")


def test_build_properties_writes_suggested_action():
    extraction = Extraction(main_point="x", suggested_action="Install uv and try one project")
    props = notion_writer._build_properties(_reel(), extraction, "done", None, None, None)
    assert props["Suggested action"]["rich_text"][0]["text"]["content"] == \
        "Install uv and try one project"


def test_build_properties_omits_suggested_action_when_empty():
    props = notion_writer._build_properties(_reel(), Extraction(main_point="x"), "done", None, None, None)
    assert "Suggested action" not in props


def test_extract_saves_fields_reads_suggested_action_back():
    page = {
        "id": "pg1", "url": "https://notion.so/pg1",
        "properties": {
            "Shortcode": {"rich_text": [{"plain_text": "SA1"}]},
            "Title": {"title": [{"plain_text": "A title"}]},
            "Status": {"select": {"name": "📥 Inbox"}},
            "Suggested action": {"rich_text": [{"plain_text": "Clone the repo and run the demo"}]},
        },
    }
    fields = notion_writer.extract_saves_fields(page)
    assert fields["suggested_action"] == "Clone the repo and run the demo"


# --- Obsidian "## Do" line -------------------------------------------------------

def _fields(action=""):
    return {
        "shortcode": "SA1", "title": "T", "status": "📥 Inbox", "priority": "High",
        "value_score": "4", "topics": [], "url": "", "posted": "2026-07-24",
        "gate_resource": "", "suggested_action": action, "page_id": "pg1",
    }


def test_note_renders_do_section_for_real_action():
    note = obsidian_sync.build_note(_fields("Install X and test on one clip"), None, "body", [])
    assert "## Do\n\nInstall X and test on one clip" in note


def test_note_skips_do_section_for_informational_and_empty():
    assert "## Do" not in obsidian_sync.build_note(_fields("none — informational"), None, "body", [])
    assert "## Do" not in obsidian_sync.build_note(_fields(""), None, "body", [])


# --- backfill script -------------------------------------------------------------

def _page(shortcode, title, status, action=None):
    return {
        "id": f"pg-{shortcode}", "url": f"https://notion.so/pg-{shortcode}",
        "properties": {
            "Shortcode": {"rich_text": [{"plain_text": shortcode}]},
            "Title": {"title": [{"plain_text": title}]},
            "Status": {"select": {"name": status}},
            "Suggested action": {"rich_text": [{"plain_text": action}] if action else []},
        },
    }


def test_backfill_selects_only_real_rows_without_an_action(monkeypatch):
    pages = [
        _page("REAL1", "A real title", "📥 Inbox"),                      # yes
        _page("HASIT1", "Another", "📥 Inbox", action="Do the thing"),   # no: already has one
        _page("PLACE1", bsa.PLACEHOLDER_TITLE, "📥 Inbox"),              # no: placeholder
        _page("PHOTO1", "A title", bsa.PHOTO_MANUAL_LABEL),              # no: photo-manual
    ]
    monkeypatch.setattr(notion_writer, "find_saves_pages_since", lambda iso: pages)
    rows = bsa.find_backfill_rows()
    assert [r["shortcode"] for r in rows] == ["REAL1"]


def _rows(*shortcodes):
    return [{"shortcode": s, "page_id": f"pg-{s}", "title": f"title {s}"} for s in shortcodes]


def test_backfill_dry_run_calls_nothing(tmp_path):
    called = []
    summary = bsa.run_backfill(
        _rows("A1"), str(tmp_path / "p.json"), dry_run=True,
        suggest_fn=lambda t, c: called.append(t) or "x",
        caption_fn=lambda pid: "", write_fn=lambda pid, a: None, print_fn=lambda m: None,
    )
    assert called == []
    assert summary["written"] == 0


def test_backfill_writes_and_resumes(tmp_path):
    progress_file = str(tmp_path / "p.json")
    written = []
    bsa.run_backfill(
        _rows("A1", "B2"), progress_file,
        suggest_fn=lambda t, c: f"do {t}", caption_fn=lambda pid: "cap",
        write_fn=lambda pid, a: written.append((pid, a)), print_fn=lambda m: None,
    )
    assert len(written) == 2
    # re-run skips both
    written.clear()
    summary = bsa.run_backfill(
        _rows("A1", "B2"), progress_file,
        suggest_fn=lambda t, c: "x", caption_fn=lambda pid: "",
        write_fn=lambda pid, a: written.append(a), print_fn=lambda m: None,
    )
    assert written == []
    assert summary["skipped"] == 2


def test_backfill_quota_stop_halts_cleanly(tmp_path):
    def boom(title, caption):
        raise bsa.QuotaExhausted("429 RESOURCE_EXHAUSTED")

    attempted = []
    summary = bsa.run_backfill(
        _rows("Q1", "NEVER1"), str(tmp_path / "p.json"),
        suggest_fn=lambda t, c: attempted.append(t) or boom(t, c),
        caption_fn=lambda pid: "", write_fn=lambda pid, a: None, print_fn=lambda m: None,
    )
    assert len(attempted) == 1
    assert summary["quota_stopped"] is True
    assert summary["written"] == 0


def test_backfill_stops_on_time_budget_deadline(tmp_path):
    """PROGRESS.md 2026-08-16: this task is now LOCAL-routed and free of
    Gemini's call-count budget, so daily_runner caps it by elapsed wall-clock
    time instead -- see the `deadline` param."""
    import time

    attempted = []
    summary = bsa.run_backfill(
        _rows("A1", "NEVER1"), str(tmp_path / "p.json"),
        suggest_fn=lambda t, c: attempted.append(t) or "x", caption_fn=lambda pid: "",
        write_fn=lambda pid, a: None, print_fn=lambda m: None,
        deadline=time.monotonic() - 1,  # already past
    )
    assert attempted == []
    assert summary["time_stopped"] is True
    assert summary["written"] == 0


def test_backfill_ollama_unavailable_halts_cleanly(tmp_path):
    """Phase 5 hard boundary: Ollama down must stop the batch cleanly, never
    fall back to Gemini."""
    from app.local_llm import OllamaUnavailable

    def boom(title, caption):
        raise OllamaUnavailable("ollama not running")

    attempted = []
    summary = bsa.run_backfill(
        _rows("O1", "NEVER1"), str(tmp_path / "p.json"),
        suggest_fn=lambda t, c: attempted.append(t) or boom(t, c),
        caption_fn=lambda pid: "", write_fn=lambda pid, a: None, print_fn=lambda m: None,
    )
    assert len(attempted) == 1
    assert summary["ollama_stopped"] is True
    assert summary["quota_stopped"] is False
    assert summary["written"] == 0


def test_suggest_action_stays_on_gemini():
    """PROGRESS.md 2026-08-16 Phase 4: unlike its four sibling text-only
    tasks, this one deliberately stayed on Gemini -- a live 3-row comparison
    found local (llama3.1:8b) output materially worse (one flatly wrong
    answer, two generic where Gemini's were specific)."""
    from app import llm_router

    assert llm_router.provider_for("suggested_action_backfill") == llm_router.GEMINI


def test_suggest_action_routes_through_llm_router(monkeypatch):
    from app import llm_router

    captured = {}

    def _fake_generate_text(task, prompt, **kwargs):
        captured["task"] = task
        captured["prompt"] = prompt
        return "Install X and try it on one clip"

    monkeypatch.setattr(llm_router, "generate_text", _fake_generate_text)

    action = bsa.suggest_action("A title", "A caption")

    assert action == "Install X and try it on one clip"
    assert captured["task"] == "suggested_action_backfill"
    assert "A title" in captured["prompt"]


def test_suggest_action_reraises_ollama_unavailable_never_falls_back_to_gemini(monkeypatch):
    """Phase 5 hard boundary: never silently retry via Gemini on a local
    provider outage."""
    import pytest

    from app import llm_router
    from app.local_llm import OllamaUnavailable

    monkeypatch.setattr(llm_router, "generate_text",
                         lambda task, prompt, **k: (_ for _ in ()).throw(OllamaUnavailable("down")))

    with pytest.raises(OllamaUnavailable):
        bsa.suggest_action("title", "caption")
