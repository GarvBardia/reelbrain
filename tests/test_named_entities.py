"""Phase G: named_entities persisted to Notion, the candidate pool, and
Obsidian — the prerequisite that made attach_matching's most specific signal
actually do anything. All mocked."""
from app import notion_writer, obsidian_sync
from app.models import Extraction, ReelData
from scripts import backfill_named_entities as bne


# --- the Notion multi_select comma constraint (learned live) ----------------------

def test_commas_are_replaced_because_notion_rejects_them():
    """REGRESSION (live): Notion rejects a multi_select option containing a
    comma -- and rejects the WHOLE property write, so ONE bad entity silently
    cost every entity on that row. Seen with
    "Agent answers leads, books calls, and runs follow ups"."""
    out = notion_writer._multi_select_names(["Agent answers leads, books calls, and runs follow ups"])
    assert out == ["Agent answers leads · books calls · and runs follow ups"]
    assert "," not in out[0]


def test_blank_and_duplicate_entities_dropped():
    assert notion_writer._multi_select_names(["Claude", "claude", "", "   ", "Claude"]) == ["Claude"]


def test_entity_names_are_length_capped():
    assert len(notion_writer._multi_select_names(["x" * 500])[0]) == 100


def test_limit_is_respected():
    assert len(notion_writer._multi_select_names([f"e{i}" for i in range(50)], limit=10)) == 10


# --- write path --------------------------------------------------------------------

def _reel():
    return ReelData(shortcode="NE1", permalink="https://www.instagram.com/reel/NE1/")


def test_build_properties_writes_named_entities():
    extraction = Extraction(main_point="x", named_entities=["Firecrawl", "Claude Code"])
    props = notion_writer._build_properties(_reel(), extraction, "done", None, None, None)
    assert [o["name"] for o in props["Named entities"]["multi_select"]] == ["Firecrawl", "Claude Code"]


def test_build_properties_omits_named_entities_when_empty():
    props = notion_writer._build_properties(_reel(), Extraction(main_point="x"), "done", None, None, None)
    assert "Named entities" not in props


def test_build_properties_sanitizes_commas_end_to_end():
    extraction = Extraction(main_point="x", named_entities=["costs $4,000 a month"])
    props = notion_writer._build_properties(_reel(), extraction, "done", None, None, None)
    assert "," not in props["Named entities"]["multi_select"][0]["name"]


# --- read paths ---------------------------------------------------------------------

def _page(entities):
    return {
        "id": "pg1", "url": "u",
        "properties": {
            "Shortcode": {"rich_text": [{"plain_text": "NE1"}]},
            "Title": {"title": [{"plain_text": "T"}]},
            "Status": {"select": {"name": "📥 Inbox"}},
            "Topics": {"multi_select": []},
            "Named entities": {"multi_select": [{"name": e} for e in entities]},
        },
    }


def test_extract_saves_fields_reads_named_entities():
    assert notion_writer.extract_saves_fields(_page(["Firecrawl"]))["named_entities"] == ["Firecrawl"]


def test_extract_digest_fields_reads_named_entities():
    """This is the one that feeds the /attach candidate pool."""
    assert notion_writer.extract_digest_fields(_page(["GSAP", "Lenis"]))["named_entities"] == ["GSAP", "Lenis"]


def test_missing_property_reads_as_empty_list():
    page = {"id": "p", "properties": {"Shortcode": {"rich_text": [{"plain_text": "X"}]}}}
    assert notion_writer.extract_saves_fields(page)["named_entities"] == []


# --- Obsidian -------------------------------------------------------------------------

def _fields(entities=()):
    return {
        "shortcode": "NE1", "title": "T", "status": "📥 Inbox", "priority": "High",
        "value_score": "4", "topics": [], "url": "https://x", "posted": "2026-07-24",
        "gate_resource": "", "suggested_action": "", "plain_summary": "",
        "named_entities": list(entities), "page_id": "pg1",
    }


def test_note_renders_named_entities_in_frontmatter_and_body():
    note = obsidian_sync.build_note(_fields(["Firecrawl", "GSAP"]), None, "body", [])
    assert "named_entities: [Firecrawl, GSAP]" in note
    assert "## Named entities" in note and "Firecrawl, GSAP" in note


def test_note_omits_named_entities_section_when_empty():
    note = obsidian_sync.build_note(_fields(), None, "body", [])
    assert "## Named entities" not in note
    assert "named_entities:" not in note


# --- backfill ---------------------------------------------------------------------------

def test_backfill_skips_placeholder_and_permalink_rows():
    from scripts.notion_deep_clean import PLACEHOLDER_TITLE

    def page(sc, title, entities=()):
        p = _page(list(entities))
        p["properties"]["Shortcode"] = {"rich_text": [{"plain_text": sc}]}
        p["properties"]["Title"] = {"title": [{"plain_text": title}]}
        return p

    pages = [
        page("REAL1", "A real extracted title"),
        page("HAS1", "Another", entities=["Claude"]),
        page("PLACE1", PLACEHOLDER_TITLE),
        page("BARE1", "https://www.instagram.com/reel/BARE1/"),
    ]
    assert [r["shortcode"] for r in bne.find_rows_needing_entities(pages)] == ["REAL1"]


def test_backfill_records_none_as_terminal_so_it_is_not_retried_forever(tmp_path):
    prog = str(tmp_path / "p.json")
    rows = [{"shortcode": "A1", "page_id": "pg", "title": "t"}]
    bne.run_backfill(rows, prog, derive_fn=lambda t, d: [], body_fn=lambda p: "",
                     write_fn=lambda p, e: None, print_fn=lambda m: None)
    attempted = []
    summary = bne.run_backfill(rows, prog, derive_fn=lambda t, d: attempted.append(t) or [],
                               body_fn=lambda p: "", write_fn=lambda p, e: None,
                               print_fn=lambda m: None)
    assert attempted == [] and summary["skipped"] == 1


def test_backfill_quota_stop_halts_cleanly(tmp_path):
    def boom(title, details):
        raise bne.QuotaExhausted("429 RESOURCE_EXHAUSTED")

    rows = [{"shortcode": "Q1", "page_id": "p", "title": "t"},
            {"shortcode": "N1", "page_id": "p", "title": "t"}]
    attempted = []
    summary = bne.run_backfill(
        rows, str(tmp_path / "p.json"),
        derive_fn=lambda t, d: attempted.append(t) or boom(t, d),
        body_fn=lambda p: "", write_fn=lambda p, e: None, print_fn=lambda m: None)
    assert len(attempted) == 1 and summary["quota_stopped"] is True
