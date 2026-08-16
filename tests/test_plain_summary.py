"""Phase C: plain_summary — schema, prompts, Notion property, Obsidian
templates, topic descriptions, and the backfill script. All mocked."""
from pathlib import Path

import pytest

from app import notion_writer, obsidian_sync, topic_descriptions
from app.models import Extraction, ReelData
from scripts import backfill_plain_summary as bps

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


# --- prompts ----------------------------------------------------------------------

@pytest.mark.parametrize("name", ["extraction.md", "extraction_caption_only.md",
                                  "extraction_carousel.md"])
def test_every_prompt_demands_plain_summary_and_standalone_main_point(name):
    text = (PROMPTS / name).read_text(encoding="utf-8")
    assert "plain_summary" in text
    assert "never heard of" in text.lower()


@pytest.mark.parametrize("name", ["extraction.md", "extraction_caption_only.md"])
def test_prompts_carry_the_jargon_gloss_example(name):
    text = (PROMPTS / name).read_text(encoding="utf-8")
    assert "MCP" in text and "STANDS ALONE" in text


# --- schema + Notion property -----------------------------------------------------

def test_plain_summary_defaults_empty_so_old_rows_still_validate():
    assert Extraction(main_point="x").plain_summary == ""


def test_build_properties_writes_plain_summary():
    reel = ReelData(shortcode="P1", permalink="https://www.instagram.com/reel/P1/")
    extraction = Extraction(main_point="x", plain_summary="It lets Claude use your other apps.")
    props = notion_writer._build_properties(reel, extraction, "done", None, None, None)
    assert props["Plain summary"]["rich_text"][0]["text"]["content"] == \
        "It lets Claude use your other apps."


def test_build_properties_omits_plain_summary_when_empty():
    reel = ReelData(shortcode="P1", permalink="https://www.instagram.com/reel/P1/")
    props = notion_writer._build_properties(reel, Extraction(main_point="x"), "done", None, None, None)
    assert "Plain summary" not in props


def test_extract_saves_fields_reads_plain_summary_back():
    page = {
        "id": "pg1", "url": "u",
        "properties": {
            "Shortcode": {"rich_text": [{"plain_text": "P1"}]},
            "Title": {"title": [{"plain_text": "T"}]},
            "Status": {"select": {"name": "📥 Inbox"}},
            "Plain summary": {"rich_text": [{"plain_text": "Plain words here."}]},
        },
    }
    assert notion_writer.extract_saves_fields(page)["plain_summary"] == "Plain words here."


# --- Obsidian note: plain summary is the FIRST thing in the body ------------------

def _fields(plain=""):
    return {
        "shortcode": "P1", "title": "Some technical title", "status": "📥 Inbox",
        "priority": "High", "value_score": "4", "topics": [], "url": "",
        "posted": "2026-07-24", "gate_resource": "", "suggested_action": "",
        "plain_summary": plain, "page_id": "pg1",
    }


def test_note_leads_with_plain_summary():
    note = obsidian_sync.build_note(_fields("It connects Claude to your apps."), None, "body", [])
    body = note.split("# Some technical title", 1)[1]
    assert body.lstrip().startswith("> It connects Claude to your apps.")


def test_note_without_plain_summary_is_unchanged():
    note = obsidian_sync.build_note(_fields(""), None, "body", [])
    assert ">" not in note.split("# Some technical title", 1)[1].split("body")[0]


# --- topic listing prefers plain_summary ------------------------------------------

def test_topic_listing_uses_plain_summary_when_present():
    entry = {"stem": "s", "title": "T", "value_score": 4, "posted": "2026-07-01",
             "main_point": "Chains MCP agents.", "plain_summary": "Lets Claude use other apps.",
             "priority": "High"}
    assert "Lets Claude use other apps." in obsidian_sync._format_entry_line(entry)


def test_topic_listing_falls_back_to_main_point():
    entry = {"stem": "s", "title": "T", "value_score": 4, "posted": "2026-07-01",
             "main_point": "Chains MCP agents.", "plain_summary": "", "priority": "High"}
    assert "Chains MCP agents." in obsidian_sync._format_entry_line(entry)


# --- topic descriptions ------------------------------------------------------------

def test_known_topic_has_a_plain_description():
    desc = topic_descriptions.describe("mcp-servers")
    assert "plug-in" in desc.lower()
    assert len(desc.split()) >= 15


def test_unknown_topic_returns_empty_never_a_guess():
    assert topic_descriptions.describe("some-topic-nobody-defined") == ""


def test_topic_note_renders_what_this_covers(tmp_path):
    (tmp_path / "topics").mkdir()
    entries = [{"stem": "s", "title": "T", "value_score": 4, "posted": "2026-07-01",
                "main_point": "mp", "plain_summary": "", "priority": "High"}]
    path = obsidian_sync.write_stub_index(tmp_path, "topics", "mcp-servers", entries)
    content = path.read_text(encoding="utf-8")
    assert "### What this covers" in content
    assert "plug-in" in content.lower()


def test_topic_note_without_description_has_no_empty_section(tmp_path):
    (tmp_path / "topics").mkdir()
    entries = [{"stem": "s", "title": "T", "value_score": 4, "posted": "2026-07-01",
                "main_point": "mp", "plain_summary": "", "priority": "High"}]
    path = obsidian_sync.write_stub_index(tmp_path, "topics", "totally-unknown-topic", entries)
    assert "### What this covers" not in path.read_text(encoding="utf-8")


# --- backfill script ----------------------------------------------------------------

def _page(shortcode, title, plain=None):
    return {
        "id": f"pg-{shortcode}",
        "properties": {
            "Shortcode": {"rich_text": [{"plain_text": shortcode}]},
            "Title": {"title": [{"plain_text": title}]},
            "Status": {"select": {"name": "📥 Inbox"}},
            "Topics": {"multi_select": []},
            "Plain summary": {"rich_text": [{"plain_text": plain}] if plain else []},
        },
    }


def test_backfill_selects_only_real_rows_without_a_summary():
    pages = [
        _page("REAL1", "A real extracted title"),
        _page("HAS1", "Another", plain="already explained"),
        _page("PLACE1", bps.PLACEHOLDER_TITLE),
        _page("BARE1", "https://www.instagram.com/reel/BARE1/"),
    ]
    rows = bps.find_rows_needing_plain_summary(pages)
    assert [r["shortcode"] for r in rows] == ["REAL1"]


def test_backfill_dry_run_calls_nothing(tmp_path):
    called = []
    summary = bps.run_backfill(
        [{"shortcode": "A1", "page_id": "pg", "title": "t", "topics": []}],
        str(tmp_path / "p.json"), dry_run=True,
        summarize_fn=lambda t, tp: called.append(t) or "x",
        write_fn=lambda pid, s: None, print_fn=lambda m: None,
    )
    assert called == [] and summary["written"] == 0


def test_backfill_writes_and_resumes(tmp_path):
    prog = str(tmp_path / "p.json")
    rows = [{"shortcode": "A1", "page_id": "pg-A1", "title": "t", "topics": []}]
    written = []
    bps.run_backfill(rows, prog, summarize_fn=lambda t, tp: "plain words",
                     write_fn=lambda pid, s: written.append((pid, s)), print_fn=lambda m: None)
    assert written == [("pg-A1", "plain words")]
    written.clear()
    summary = bps.run_backfill(rows, prog, summarize_fn=lambda t, tp: "x",
                               write_fn=lambda pid, s: written.append(s), print_fn=lambda m: None)
    assert written == [] and summary["skipped"] == 1


def test_backfill_quota_stop_halts_cleanly(tmp_path):
    def boom(title, topics):
        raise bps.QuotaExhausted("429 RESOURCE_EXHAUSTED")

    rows = [{"shortcode": "Q1", "page_id": "p", "title": "t", "topics": []},
            {"shortcode": "N1", "page_id": "p", "title": "t", "topics": []}]
    attempted = []
    summary = bps.run_backfill(
        rows, str(tmp_path / "p.json"),
        summarize_fn=lambda t, tp: attempted.append(t) or boom(t, tp),
        write_fn=lambda pid, s: None, print_fn=lambda m: None,
    )
    assert len(attempted) == 1
    assert summary["quota_stopped"] is True and summary["written"] == 0


def test_backfill_stops_on_time_budget_deadline(tmp_path):
    """PROGRESS.md 2026-08-16: this task is now LOCAL-routed and free of
    Gemini's call-count budget, so daily_runner caps it by elapsed wall-clock
    time instead -- see the `deadline` param."""
    import time

    rows = [{"shortcode": "A1", "page_id": "p", "title": "t", "topics": []},
            {"shortcode": "NEVER1", "page_id": "p", "title": "t", "topics": []}]
    attempted = []
    summary = bps.run_backfill(
        rows, str(tmp_path / "p.json"),
        summarize_fn=lambda t, tp: attempted.append(t) or "x",
        write_fn=lambda pid, s: None, print_fn=lambda m: None,
        deadline=time.monotonic() - 1,  # already past
    )
    assert attempted == []
    assert summary["time_stopped"] is True
    assert summary["written"] == 0


def test_backfill_ollama_unavailable_halts_cleanly(tmp_path):
    """Phase 5 hard boundary: Ollama down must stop the batch cleanly, never
    fall back to Gemini."""
    from app.local_llm import OllamaUnavailable

    def boom(title, topics):
        raise OllamaUnavailable("ollama not running")

    rows = [{"shortcode": "O1", "page_id": "p", "title": "t", "topics": []},
            {"shortcode": "NEVER1", "page_id": "p", "title": "t", "topics": []}]
    attempted = []
    summary = bps.run_backfill(
        rows, str(tmp_path / "p.json"),
        summarize_fn=lambda t, tp: attempted.append(t) or boom(t, tp),
        write_fn=lambda pid, s: None, print_fn=lambda m: None,
    )
    assert len(attempted) == 1
    assert summary["ollama_stopped"] is True
    assert summary["quota_stopped"] is False
    assert summary["written"] == 0


def test_summarize_plainly_is_routed_as_a_local_task():
    """PROGRESS.md 2026-08-16: this is one of the five tasks moved to local
    Ollama specifically to stop costing Gemini quota."""
    from app import llm_router

    assert llm_router.provider_for("plain_summary_backfill") == llm_router.LOCAL


def test_summarize_plainly_routes_through_llm_router(monkeypatch):
    from app import llm_router

    captured = {}

    def _fake_generate_text(task, prompt, **kwargs):
        captured["task"] = task
        captured["prompt"] = prompt
        return "It lets you do a thing easily."

    monkeypatch.setattr(llm_router, "generate_text", _fake_generate_text)

    summary = bps.summarize_plainly("A title", ["claude-ai"])

    assert summary == "It lets you do a thing easily."
    assert captured["task"] == "plain_summary_backfill"
    assert "A title" in captured["prompt"]


def test_summarize_plainly_reraises_ollama_unavailable_never_falls_back_to_gemini(monkeypatch):
    """Phase 5 hard boundary: never silently retry via Gemini on a local
    provider outage."""
    from app import llm_router
    from app.local_llm import OllamaUnavailable

    monkeypatch.setattr(llm_router, "generate_text",
                         lambda task, prompt, **k: (_ for _ in ()).throw(OllamaUnavailable("down")))

    with pytest.raises(OllamaUnavailable):
        bps.summarize_plainly("title", [])
