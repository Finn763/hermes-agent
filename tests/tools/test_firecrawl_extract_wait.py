"""Firecrawl scrape must wait for lazily loaded content (#106904).

Without ``waitFor`` the scrape snapshots first paint: GitHub issue
comments, collapsed sections, dashboards all arrive after the snapshot
and the partial result reads as complete. These tests assert the wait
actually reaches the scrape call (SDK kwargs and keyless REST payload),
not just that a config key exists.
"""

import asyncio

import plugins.web.firecrawl.provider as fc


def _run(coro):
    return asyncio.run(coro)


class _FakeScrapeClient:
    """Duck-types the SDK scrape; captures kwargs for assertions."""

    def __init__(self):
        self.calls = []

    def scrape(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "markdown": "# hello",
            "metadata": {"title": "t", "sourceURL": "https://example.com/x"},
        }


def _patch(monkeypatch, client, web_cfg):
    import tools.web_tools as wt

    monkeypatch.setattr(fc, "_get_firecrawl_client", lambda: client)
    monkeypatch.setattr(fc, "_use_keyless_ring", lambda: False)
    monkeypatch.setattr(fc, "check_website_access", lambda url: None)
    monkeypatch.setattr(fc, "is_safe_url", lambda url: True)
    monkeypatch.setattr("tools.interrupt.is_interrupted", lambda: False)
    monkeypatch.setattr(wt, "_load_web_config", lambda: dict(web_cfg))


def _wait_of(call):
    for key in ("wait_for", "waitFor", "wait_ms"):
        if key in call:
            return call[key]
    return None


def test_default_wait_reaches_sdk_scrape(monkeypatch):
    """No config -> 3000ms wait is still sent (first paint is not enough)."""
    client = _FakeScrapeClient()
    _patch(monkeypatch, client, {})
    _run(fc.FirecrawlWebSearchProvider().extract(["https://example.com/x"]))
    assert client.calls, "scrape was never called"
    assert _wait_of(client.calls[0]) == 3000, client.calls[0]


def test_config_override_reaches_sdk_scrape(monkeypatch):
    client = _FakeScrapeClient()
    _patch(monkeypatch, client, {"extract_wait_ms": 8000})
    _run(fc.FirecrawlWebSearchProvider().extract(["https://example.com/x"]))
    assert _wait_of(client.calls[0]) == 8000, client.calls[0]


def test_zero_disables_wait(monkeypatch):
    """extract_wait_ms=0 restores legacy first-paint behavior."""
    client = _FakeScrapeClient()
    _patch(monkeypatch, client, {"extract_wait_ms": 0})
    _run(fc.FirecrawlWebSearchProvider().extract(["https://example.com/x"]))
    assert _wait_of(client.calls[0]) is None, client.calls[0]


def test_invalid_config_falls_back_to_default(monkeypatch):
    client = _FakeScrapeClient()
    _patch(monkeypatch, client, {"extract_wait_ms": "junk"})
    _run(fc.FirecrawlWebSearchProvider().extract(["https://example.com/x"]))
    assert _wait_of(client.calls[0]) == 3000, client.calls[0]


def _keyless_body(monkeypatch, web_cfg, *, via_caller=True):
    """The JSON body the keyless REST client posts, optionally via _scrape_with_wait."""
    import tools.web_tools as wt

    captured = {}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {}

    def _fake_post(url, **kwargs):
        captured.update(kwargs.get("json", {}))
        return _Response()

    monkeypatch.setattr(wt, "_load_web_config", lambda: dict(web_cfg))
    monkeypatch.setattr(fc.httpx, "post", _fake_post)
    scrape = fc._KeylessFirecrawlClient().scrape
    if via_caller:
        fc._scrape_with_wait(scrape, url="https://example.com", formats=["markdown"])
    else:
        scrape(url="https://example.com", formats=["markdown"])
    return captured


def test_keyless_scrape_honours_extract_wait_ms(monkeypatch):
    """The keyless leg honours the config, including the documented 0 escape hatch."""
    assert _keyless_body(monkeypatch, {})["waitFor"] == 3000
    assert _keyless_body(monkeypatch, {"extract_wait_ms": 8000})["waitFor"] == 8000
    assert "waitFor" not in _keyless_body(monkeypatch, {"extract_wait_ms": 0})
    assert "waitFor" not in _keyless_body(monkeypatch, {"extract_wait_ms": -5})
    assert _keyless_body(monkeypatch, {"extract_wait_ms": "junk"})["waitFor"] == 3000


def test_keyless_scrape_without_a_wait_kwarg_sends_no_wait(monkeypatch):
    """A caller that passed no wait must not have 3000ms re-invented for it."""
    assert "waitFor" not in _keyless_body(monkeypatch, {}, via_caller=False)


def test_keyless_ring_leg_honours_extract_wait_ms(monkeypatch):
    """The keyless ring's firecrawl extract waits per config, and 0 still disables."""
    import tools.web_tools as wt
    from plugins.web.keyless_mcp import firecrawl_extract_keyless

    seen = []

    class _Client:
        def scrape(self, **kwargs):
            seen.append(kwargs)
            return {"data": {"markdown": "# hi"}}

    monkeypatch.setattr(fc, "_KeylessFirecrawlClient", lambda: _Client())

    def _wait_for_config(cfg):
        seen.clear()
        monkeypatch.setattr(wt, "_load_web_config", lambda: dict(cfg))
        firecrawl_extract_keyless(["https://example.com"])
        return _wait_of(seen[0])

    assert _wait_for_config({}) == 3000
    assert _wait_for_config({"extract_wait_ms": 0}) is None


def test_search_sends_no_wait(monkeypatch):
    """The wait applies to scrape/extract only, never to search."""
    seen = {}

    class _FakeSearchClient:
        def search(self, **kwargs):
            seen.update(kwargs)
            return {"data": []}

    import tools.web_tools as wt

    monkeypatch.setattr(fc, "_get_firecrawl_client", lambda: _FakeSearchClient())
    monkeypatch.setattr(fc, "_use_keyless_ring", lambda: False)
    monkeypatch.setattr("tools.interrupt.is_interrupted", lambda: False)
    monkeypatch.setattr(wt, "_load_web_config", lambda: {})
    fc.FirecrawlWebSearchProvider().search("hello", limit=3)
    assert _wait_of(seen) is None, seen
