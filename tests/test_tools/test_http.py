"""Tests for HTTP tool."""

import pytest

from codeassist.tools.http import HTTPTool


class TestHTTPTool:
    """Test HTTP tool operations."""

    @pytest.fixture
    def http_tool(self):
        """Create an HTTPTool instance."""
        return HTTPTool()

    @pytest.mark.asyncio
    async def test_invalid_url(self, http_tool):
        """Test that invalid URL returns error."""
        result = await http_tool.execute(method="GET", url="not-a-url")
        
        assert "Error" in result.output and "http://" in result.output

    @pytest.mark.asyncio
    async def test_schema(self, http_tool):
        """Test that HTTP tool has proper schema."""
        schema = http_tool.schema()
        
        assert schema["name"] == "http"
        assert "description" in schema
        assert "parameters" in schema
        assert "method" in schema["parameters"]["properties"]
        assert "url" in schema["parameters"]["properties"]

    @pytest.mark.asyncio
    async def test_required_parameters(self, http_tool):
        """Test that method and url are required."""
        # This should fail because url is missing
        with pytest.raises(TypeError):
            await http_tool.execute(method="GET")

    def test_shared_client_follows_redirects(self):
        """The HTTP tool relies on the shared client, which follows redirects."""
        from codeassist.tools.webfetch import _get_fetch_client

        assert _get_fetch_client().follow_redirects is True

    @pytest.mark.asyncio
    async def test_redirects_are_followed(self, http_tool, monkeypatch):
        """A redirect response is followed and the final body returned."""
        import httpx

        from codeassist.tools import http as http_module
        from codeassist.tools import webfetch

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/start":
                return httpx.Response(
                    302, headers={"location": "https://example.com/end"}
                )
            return httpx.Response(
                200, headers={"content-type": "text/plain"}, text="arrived"
            )

        monkeypatch.setattr(http_module, "validate_url", lambda url: True)
        old_client = webfetch._fetch_client
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=True
        )
        webfetch._fetch_client = client
        try:
            result = await http_tool.execute(
                method="GET", url="https://example.com/start"
            )
        finally:
            webfetch._fetch_client = old_client
            await client.aclose()

        assert result.error is False
        assert "arrived" in result.output
