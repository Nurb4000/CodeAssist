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
