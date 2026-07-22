from app import resource_lookup


def _fake_response(text, status_code=200):
    class _Resp:
        def __init__(self):
            self.status_code = status_code
            self.text = text

        def raise_for_status(self):
            if self.status_code >= 400:
                raise Exception(f"HTTP {self.status_code}")

    return _Resp()


def test_fetch_title_and_og_description(monkeypatch):
    html = (
        "<html><head><title>Scroll World Guide</title>"
        '<meta property="og:description" content="An open-source Claude skill for scroll animations.">'
        "</head><body></body></html>"
    )
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _fake_response(html))

    title, description = resource_lookup.fetch_resource_title_and_description("https://example.com")
    assert title == "Scroll World Guide"
    assert description == "An open-source Claude skill for scroll animations."


def test_fetch_falls_back_to_plain_description_meta(monkeypatch):
    html = '<html><head><title>X</title><meta name="description" content="Plain description here."></head></html>'
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _fake_response(html))

    _title, description = resource_lookup.fetch_resource_title_and_description("https://example.com")
    assert description == "Plain description here."


def test_fetch_handles_missing_title_and_description(monkeypatch):
    html = "<html><body>no title or meta tags here</body></html>"
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _fake_response(html))

    title, description = resource_lookup.fetch_resource_title_and_description("https://example.com")
    assert title == ""
    assert description == ""


def test_fetch_returns_empty_on_network_error(monkeypatch):
    import httpx

    def _boom(*a, **kw):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(httpx, "get", _boom)
    title, description = resource_lookup.fetch_resource_title_and_description("https://example.com")
    assert (title, description) == ("", "")


def test_fetch_returns_empty_on_http_error(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _fake_response("", status_code=404))

    title, description = resource_lookup.fetch_resource_title_and_description("https://example.com")
    assert (title, description) == ("", "")


def test_fetch_unescapes_html_entities_and_collapses_whitespace(monkeypatch):
    html = "<html><head><title>Tips &amp; Tricks\n   for   You</title></head></html>"
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _fake_response(html))

    title, _description = resource_lookup.fetch_resource_title_and_description("https://example.com")
    assert title == "Tips & Tricks for You"
