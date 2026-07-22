"""Task 7 — targeted gaps: real-world share-URL params, unicode in Notion writes,
empty-caption/no-speech end to end, /attach matching priority."""
import pytest
from fastapi.testclient import TestClient

from app import main, notion_writer, store
from app.fetcher import normalize_url
from app.models import CommentGate, Extraction, ReelData
from tests.test_pipeline import FakeClient, _install_fake_notion, save_page_calls

# --- 1. real-world share URL shapes -----------------------------------------

REAL_WORLD_URLS = [
    # exact shape the IG iOS share sheet produces (base64 igsh, trailing ==)
    ("https://www.instagram.com/reel/C9xAbC12345/?igsh=MzRlODBiNWFlZA==", "C9xAbC12345"),
    ("https://www.instagram.com/reel/C9xAbC12345/?utm_source=ig_web_copy_link&utm_campaign=x", "C9xAbC12345"),
    ("https://www.instagram.com/reel/C9xAbC12345/?igsh=MzRlODBiNWFlZA%3D%3D", "C9xAbC12345"),
    ("https://www.instagram.com/p/C9xAbC12345/?igsh=abc&utm_medium=share", "C9xAbC12345"),
    # share-sheet text blob around the link
    ("Check this reel by @someone https://www.instagram.com/reel/C9xAbC12345/?igsh=MzRlODBiNWFlZA== 🔥", "C9xAbC12345"),
]


@pytest.mark.parametrize("url,expected", REAL_WORLD_URLS)
def test_real_world_share_urls_normalize(url, expected):
    assert normalize_url(url) == expected


# --- 2. unicode captions / emoji in Notion writes ----------------------------

def test_unicode_caption_and_extraction_survive_notion_write(monkeypatch):
    reel = ReelData(
        shortcode="UNI001",
        permalink="https://www.instagram.com/reel/UNI001/",
        video_path="/tmp/UNI001.mp4",
        caption="देसी nuskhe 🌿 दादी माँ के — grandma's remedies 🇮🇳✨ „quotes» and emoji 🤯",
        creator_username="désí_créator",
        creator_fullname="देसी Créator 🌟",
    )
    extraction = Extraction(
        transcript="हल्दी वाला दूध पियो और सो जाओ 😴",
        has_speech=True,
        main_point="Turmeric milk before bed improves sleep 🥛✨",
        supporting_points=["दादी माँ approved 👵"],
        quotable_lines=["हल्दी वाला दूध पियो और सो जाओ 😴"],
        topic_tags=["desi-nuskhe", "health"],
        content_type="insight",
        comment_gate=CommentGate(detected=False),
        value_score=4,
    )
    fake = _install_fake_notion(monkeypatch)
    monkeypatch.setattr("app.main.fetcher.fetch_reel", lambda s, p: reel)
    monkeypatch.setattr("app.main.gemini_pipe.run_extraction", lambda r, n, t: extraction)

    store.insert_processing(reel.shortcode, reel.permalink)
    main.run_pipeline(reel.shortcode, reel.permalink, note="मेरा note 📝")

    call = save_page_calls(fake)[0]
    props = call["properties"]
    assert props["Title"]["title"][0]["text"]["content"] == "Turmeric milk before bed improves sleep 🥛✨"
    assert props["My note"]["rich_text"][0]["text"]["content"] == "मेरा note 📝"
    quote_blocks = [b for b in call["children"] if b["type"] == "quote"]
    assert quote_blocks[0]["quote"]["rich_text"][0]["text"]["content"] == "हल्दी वाला दूध पियो और सो जाओ 😴"
    row = store.get_by_shortcode(reel.shortcode)
    assert row["transcript"] == "हल्दी वाला दूध पियो और सो जाओ 😴"


def test_rich_text_truncation_is_unicode_safe():
    # 2000-char cap sliced on a str of emoji must not raise or split escapes
    text = "🤯" * 3000
    out = notion_writer._rich_text(text)
    assert len(out[0]["text"]["content"]) == 2000


# --- 3. empty caption + no speech, end to end --------------------------------

def test_empty_caption_no_speech_reel_end_to_end(monkeypatch):
    reel = ReelData(
        shortcode="EMPTY001",
        permalink="https://www.instagram.com/reel/EMPTY001/",
        video_path="/tmp/EMPTY001.mp4",
        caption=None,
        creator_username="silentposter",
    )
    extraction = Extraction(
        transcript="",
        has_speech=False,
        main_point="(no caption)",
        content_type="entertainment",
        comment_gate=CommentGate(detected=False),
        value_score=1,
    )
    fake = _install_fake_notion(monkeypatch)
    monkeypatch.setattr("app.main.fetcher.fetch_reel", lambda s, p: reel)
    monkeypatch.setattr("app.main.gemini_pipe.run_extraction", lambda r, n, t: extraction)

    store.insert_processing(reel.shortcode, reel.permalink)
    main.run_pipeline(reel.shortcode, reel.permalink, note=None)

    call = save_page_calls(fake)[0]
    toggles = {b["toggle"]["rich_text"][0]["text"]["content"]: b for b in call["children"] if b["type"] == "toggle"}
    transcript_body = toggles["Transcript"]["toggle"]["children"][0]["paragraph"]["rich_text"][0]["text"]["content"]
    caption_body = toggles["Raw caption"]["toggle"]["children"][0]["paragraph"]["rich_text"][0]["text"]["content"]
    assert transcript_body == "(no speech detected)"
    assert caption_body == "(no caption)"
    # value 1 -> low signal, and nothing invented anywhere
    assert call["properties"]["Status"]["select"]["name"] == "🗑 Low signal"
    assert store.get_by_shortcode(reel.shortcode)["status"] == "low_signal"


# --- 4. /attach fallback-matching priority order ------------------------------

def _seed_awaiting(shortcode: str, note: str | None = None) -> None:
    store.insert_processing(shortcode, f"https://www.instagram.com/reel/{shortcode}/", note=note)
    store.update_save(shortcode, status="awaiting_dm", notion_page_id=f"page-{shortcode}",
                      notion_page_url=f"https://notion.so/page-{shortcode}")


def _attach_client(monkeypatch):
    monkeypatch.setattr(main, "CAPTURE_SECRET", "test-secret")
    monkeypatch.setattr(notion_writer, "_client", lambda: FakeClient())
    return TestClient(main.app)


def test_attach_shortcode_match_beats_note_match(monkeypatch):
    client = _attach_client(monkeypatch)
    _seed_awaiting("TRICKY", note="nothing relevant")
    # a DIFFERENT row whose note contains the literal text "TRICKY"
    _seed_awaiting("OTHER1", note="remember TRICKY wordplay here")

    resp = client.post("/attach", json={
        "shortcode_or_note": "TRICKY", "resource_url": "https://x.com/r", "secret": "test-secret",
    })
    assert resp.json()["shortcode"] == "TRICKY"  # exact shortcode wins over note substring


def test_attach_no_exact_shortcode_with_pending_rows_never_auto_commits(monkeypatch):
    """The removed behavior (see PROGRESS.md): omitting shortcode_or_note used
    to fall back to a note/title substring match or "the sole Awaiting DM
    row", auto-committing with no way to verify it was the semantically
    right one. That fallback tier is gone entirely — with no exact shortcode,
    the resource content is fetched and scored, and the result is always
    either a ranked-candidates response (409) or a clear "unresolved" (404),
    never a silent 200."""
    client = _attach_client(monkeypatch)
    from app import resource_lookup
    monkeypatch.setattr(resource_lookup, "fetch_resource_title_and_description", lambda url: ("", ""))
    _seed_awaiting("FIRST", note="alpha")
    _seed_awaiting("SECOND", note="beta")

    resp = client.post("/attach", json={
        "shortcode_or_note": None, "resource_url": "https://x.com/r", "secret": "test-secret",
    })
    assert resp.status_code == 404
    assert resp.json()["detail"]["status"] == "unresolved"
    assert store.get_by_shortcode("FIRST")["status"] == "awaiting_dm"
    assert store.get_by_shortcode("SECOND")["status"] == "awaiting_dm"


def test_attach_ignores_non_awaiting_rows_without_a_keyword(monkeypatch):
    """A plain Inbox row with no gate_keyword at all was never a candidate —
    it has no open gate to fulfill, unlike the BUG2 Inbox-with-keyword edge
    case get_attach_candidates() deliberately includes."""
    client = _attach_client(monkeypatch)
    store.insert_processing("DONE1", "https://www.instagram.com/reel/DONE1/", note="ai workflow")
    store.update_save("DONE1", status="done")

    candidates = store.get_attach_candidates()
    assert "DONE1" not in {c["shortcode"] for c in candidates}
