import logging

from app import attach_audit, notion_writer


def test_format_entry_includes_all_provided_fields():
    line = attach_audit.format_entry(
        "some note", "https://x.com/r", "needs_confirmation",
        shortcode=None, candidates=["A", "B"], detail=None,
    )
    assert "outcome=needs_confirmation" in line
    assert "input_shortcode_or_note='some note'" in line
    assert "resource_url=https://x.com/r" in line
    assert "candidates=['A', 'B']" in line


def test_record_logs_locally_even_without_notion_configured(monkeypatch, caplog):
    monkeypatch.setattr(attach_audit, "NOTION_PARENT_PAGE_ID", "")
    with caplog.at_level(logging.INFO):
        attach_audit.record(None, "https://x.com/r", "attached", shortcode="ABC123")
    assert any("outcome=attached" in r.message for r in caplog.records)


def test_record_appends_to_notion_when_configured(monkeypatch):
    monkeypatch.setattr(attach_audit, "NOTION_PARENT_PAGE_ID", "parent-page-id")
    calls = []
    monkeypatch.setattr(
        notion_writer, "append_to_named_page",
        lambda parent_id, title, blocks: calls.append((parent_id, title, blocks)),
    )

    attach_audit.record("shortcut input", "https://x.com/r", "attached", shortcode="ABC123")

    assert len(calls) == 1
    parent_id, title, blocks = calls[0]
    assert parent_id == "parent-page-id"
    assert title == attach_audit.AUDIT_LOG_TITLE
    assert len(blocks) == 1
    assert blocks[0]["type"] == "paragraph"


def test_record_never_raises_when_notion_append_fails(monkeypatch):
    monkeypatch.setattr(attach_audit, "NOTION_PARENT_PAGE_ID", "parent-page-id")

    def _boom(*a, **kw):
        raise RuntimeError("notion down")

    monkeypatch.setattr(notion_writer, "append_to_named_page", _boom)

    # must not raise -- audit logging is best-effort only
    attach_audit.record(None, "https://x.com/r", "unresolved")
