"""scripts/local_fetch.py — mocked. The reusable OG helpers it imports
(clean_og_caption / fetch_og_tags_with_bot_ua) are covered in
test_recover_photo_captions.py; here we cover its own routing (media vs OG)
and URL reading."""
from scripts import local_fetch as lf


def test_read_urls_keeps_only_instagram_lines_and_strips_bom(tmp_path):
    f = tmp_path / "urls.txt"
    # leading BOM + a header line + blanks + real URLs (CRLF)
    f.write_bytes(
        b"\xef\xbb\xbfreels\r\n"
        b"https://www.instagram.com/p/AAA/?hl=en\r\n"
        b"\r\n"
        b"# a comment, not a url\r\n"
        b"http://instagram.com/p/BBB/\r\n"
    )
    assert lf._read_urls(str(f)) == [
        "https://www.instagram.com/p/AAA/?hl=en",
        "http://instagram.com/p/BBB/",
    ]


def test_probe_uses_full_extraction_when_media_downloads(monkeypatch):
    from app import fetcher, gemini_pipe, store
    from app.models import Extraction, ReelData

    monkeypatch.setattr(store, "get_taxonomy", lambda: [])
    monkeypatch.setattr(
        lf, "_try_media_fetch",
        lambda sc, pl: ({"_video_path": "/tmp/x.mp4"}, "media downloaded"),
    )
    monkeypatch.setattr(
        fetcher, "_info_to_reel_data",
        lambda sc, pl, info: ReelData(shortcode=sc, permalink=pl, video_path="/tmp/x.mp4"),
    )
    called = {}

    def _full(reel, note, taxonomy):
        called["full"] = True
        return Extraction(main_point="real video summary", topic_tags=["ai"], value_score=5)

    monkeypatch.setattr(gemini_pipe, "run_extraction", _full)
    monkeypatch.setattr(gemini_pipe, "run_caption_only_extraction",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not use OG path")))

    result = lf.probe_one("https://www.instagram.com/p/VID1/")
    assert result["outcome"] == "extracted"
    assert result["path"].startswith("full")
    assert called.get("full") is True


def test_probe_falls_back_to_og_caption_when_no_media(monkeypatch):
    from app import gemini_pipe, store
    from app.models import Extraction

    monkeypatch.setattr(store, "get_taxonomy", lambda: [])
    monkeypatch.setattr(lf, "_try_media_fetch", lambda sc, pl: (None, "no media"))
    monkeypatch.setattr(lf, "fetch_og_tags_with_bot_ua",
                        lambda pl: {"og:title": 'X on Instagram: "a real caption here"'})
    monkeypatch.setattr(gemini_pipe, "run_caption_only_extraction",
                        lambda c, creator, note, taxonomy: Extraction(
                            main_point="caption summary", topic_tags=["design"], value_score=3))

    result = lf.probe_one("https://www.instagram.com/p/PHOTO1/")
    assert result["outcome"] == "extracted"
    assert result["path"].startswith("caption-only")


def test_probe_reports_blocked_when_no_media_and_no_og(monkeypatch):
    from app import store
    monkeypatch.setattr(store, "get_taxonomy", lambda: [])
    monkeypatch.setattr(lf, "_try_media_fetch", lambda sc, pl: (None, "no media"))
    monkeypatch.setattr(lf, "fetch_og_tags_with_bot_ua", lambda pl: None)

    result = lf.probe_one("https://www.instagram.com/p/BLOCKED1/")
    assert result["outcome"] == "blocked"


def test_probe_bad_url_is_skipped():
    assert lf.probe_one("not a url")["outcome"] == "bad_url"
