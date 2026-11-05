"""Tests for the web-search tool's deep-fetch SSRF guard."""

import pytest

from codeassist.tools.advanced import WebSearchTool


class _Resp:
    headers = {"content-type": "text/plain"}
    text = "public body"

    def raise_for_status(self):
        pass


class _Client:
    def __init__(self, calls):
        self.calls = calls

    async def get(self, url, headers=None):
        self.calls.append(url)
        return _Resp()


@pytest.mark.asyncio
async def test_deep_fetch_skips_private_urls(monkeypatch):
    """A poisoned search result must not be fetched from an internal address."""
    calls = []
    monkeypatch.setattr(
        "codeassist.tools.advanced.validate_url",
        lambda url: url.startswith("https://public.example/"),
    )
    monkeypatch.setattr(
        "codeassist.tools.webfetch._get_fetch_client", lambda: _Client(calls)
    )

    tool = WebSearchTool()
    out = await tool._deep_fetch(
        "https://public.example/a\n"
        "http://169.254.169.254/latest/meta-data/\n"
        "http://localhost:8080/admin",
        3,
    )

    # Only the public URL is fetched; the initial hop is validated, not just redirects.
    assert calls == ["https://public.example/a"]
    assert "## https://public.example/a" in out.output
    assert "public body" in out.output
    assert out.output.count("Skipped: URL targets a private or internal network") == 2


@pytest.mark.asyncio
async def test_deep_fetch_all_internal_never_contacts_client(monkeypatch):
    calls = []
    monkeypatch.setattr("codeassist.tools.advanced.validate_url", lambda url: False)
    monkeypatch.setattr(
        "codeassist.tools.webfetch._get_fetch_client", lambda: _Client(calls)
    )

    tool = WebSearchTool()
    out = await tool._deep_fetch(
        "http://127.0.0.1:9000/\nhttp://[::1]:9000/",
        2,
    )

    assert calls == []
    assert out.output.count("Skipped: URL targets a private or internal network") == 2
