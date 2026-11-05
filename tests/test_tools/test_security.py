"""Tests for tools.security URL validation.

Covers the port gate added in §15.61: validate_url must reject fetches aimed at
non-HTTP internal service ports even when the host resolves to a public IP, so a
validated host cannot be used as an SSRF vector into SSH/DB/RDP/etc.
"""
import pytest

from codeassist.tools.security import validate_url

# A genuine public (non-reserved) IP so the address check passes and only the
# port gate is under test. Literal IPs keep these deterministic (no DNS).
PUBLIC_IP = "8.8.8.8"


class TestValidateUrlPorts:
    @pytest.mark.parametrize("url", [
        f"http://{PUBLIC_IP}:443/",
        f"https://{PUBLIC_IP}:80/",
        f"http://{PUBLIC_IP}:8080/",
        f"http://{PUBLIC_IP}:8000/",
    ])
    def test_web_ports_allowed(self, url):
        assert validate_url(url) is True

    @pytest.mark.parametrize("url", [
        f"http://{PUBLIC_IP}:21/",       # FTP
        f"http://{PUBLIC_IP}:22/",       # SSH
        f"http://{PUBLIC_IP}:23/",       # Telnet
        f"http://{PUBLIC_IP}:25/",       # SMTP
        f"http://{PUBLIC_IP}:110/",      # POP3
        f"http://{PUBLIC_IP}:135/",      # MSRPC
        f"http://{PUBLIC_IP}:139/",      # NetBIOS
        f"http://{PUBLIC_IP}:445/",      # SMB
        f"http://{PUBLIC_IP}:1433/",     # MSSQL
        f"http://{PUBLIC_IP}:1521/",     # Oracle
        f"http://{PUBLIC_IP}:3306/",     # MySQL
        f"http://{PUBLIC_IP}:3389/",     # RDP
        f"http://{PUBLIC_IP}:5432/",     # PostgreSQL
        f"http://{PUBLIC_IP}:5672/",     # AMQP
        f"http://{PUBLIC_IP}:6379/",     # Redis
        f"http://{PUBLIC_IP}:9200/",     # Elasticsearch
        f"http://{PUBLIC_IP}:11211/",    # Memcached
        f"http://{PUBLIC_IP}:27017/",    # MongoDB
    ])
    def test_internal_service_ports_blocked(self, url):
        assert validate_url(url) is False

    def test_invalid_port_fails_closed(self):
        assert validate_url(f"http://{PUBLIC_IP}:99999/") is False

    @pytest.mark.parametrize("url", [
        f"http://{PUBLIC_IP}/",
        f"https://{PUBLIC_IP}/",
    ])
    def test_default_ports_allowed_when_implicit(self, url):
        # No explicit port -> 80/443 -> allowed.
        assert validate_url(url) is True


class TestDNSRebindingPin:
    """F1 regression: validate_url pins validated IPs so connect-time DNS can't
    swap to a rebind. Without pinning, the pool's connect-time lookup resolves to
    whatever DNS now answers (an attacker-chosen IP) after validation passed."""

    def test_validate_url_pins_validated_ips(self, monkeypatch):
        from codeassist.tools.security import _PINNED_IPS, validate_url

        _PINNED_IPS.__dict__.clear()
        try:
            # A public host whose resolved IPs we control via monkeypatched DNS.
            monkeypatch.setattr(
                "codeassist.tools.security.socket.getaddrinfo",
                lambda host, *a, **k: [
                    (2, 1, 6, "", ("8.8.8.8", 443)),
                ],
            )
            assert validate_url("https://pin.example.com/") is True

            pinned = _PINNED_IPS.__dict__.get("pin.example.com")
            assert pinned == ["8.8.8.8"], "validated IPs must be pinned"
        finally:
            _PINNED_IPS.__dict__.clear()

    @pytest.mark.asyncio
    async def test_backend_forces_connection_to_pinned_ip(self, monkeypatch):
        """The pinned-DNS backend connects to the pre-validated address, not free
        DNS -- so a rebind at connect time is defeated."""
        import anyio
        from codeassist.tools.security import (
            _PINNED_IPS, _PinnedDNSBackend, validate_url,
        )

        _PINNED_IPS.__dict__.clear()
        try:
            monkeypatch.setattr(
                "codeassist.tools.security.socket.getaddrinfo",
                lambda host, *a, **k: [
                    (2, 1, 6, "", ("8.8.8.8", 443)),
                ],
            )
            # Pin via validation.
            assert validate_url("https://forced.example.com/") is True
            pinned = _PINNED_IPS.__dict__.get("forced.example.com")

            # Record what anyio.connect_tcp is called with as remote_host.
            calls: list[str] = []

            async def spy(remote_host=None, remote_port=None, **kw):
                calls.append(remote_host)
                return None  # dummy stream; backend only inspects the arg

            monkeypatch.setattr("anyio.connect_tcp", spy)

            backend = _PinnedDNSBackend()
            await backend.connect_tcp("forced.example.com", 443, timeout=10)

            # Forced to the pinned IP (8.8.8.8), not free DNS ("forced.example.com").
            assert calls[0] in pinned, "connect-time resolution must hit the pinned IP"
            assert calls[0] == "8.8.8.8", "backend must connect to the validated address"
        finally:
            _PINNED_IPS.__dict__.clear()
