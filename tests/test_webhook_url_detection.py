"""Tests for app/services/webhook_url_detection.py — EVT-07 / D-12.

Returns up to 3 webhook URL candidates: Docker hostname, LAN IP from request Host header,
Tailscale 100.64.0.0/10 (CGNAT) IPv4. Pure unit tests — no FastAPI TestClient required.
"""
from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

import pytest


class TestDockerHostnameCandidate:
    @patch("app.services.webhook_url_detection.socket.gethostname")
    def test_returns_url_with_hostname(self, mock_get):
        mock_get.return_value = "composer-abc123"
        from app.services.webhook_url_detection import docker_hostname_candidate

        assert (
            docker_hostname_candidate(8085)
            == "http://composer-abc123:8085/api/webhooks/plex"
        )

    @patch("app.services.webhook_url_detection.socket.gethostname")
    def test_skips_localhost(self, mock_get):
        mock_get.return_value = "localhost"
        from app.services.webhook_url_detection import docker_hostname_candidate

        assert docker_hostname_candidate(8085) is None

    @patch("app.services.webhook_url_detection.socket.gethostname")
    def test_skips_ip_loopback(self, mock_get):
        mock_get.return_value = "127.0.0.1"
        from app.services.webhook_url_detection import docker_hostname_candidate

        assert docker_hostname_candidate(8085) is None

    @patch("app.services.webhook_url_detection.socket.gethostname")
    def test_returns_none_on_oserror(self, mock_get):
        mock_get.side_effect = OSError("boom")
        from app.services.webhook_url_detection import docker_hostname_candidate

        assert docker_hostname_candidate(8085) is None


class TestLanIpCandidate:
    def test_uses_host_header(self):
        from app.services.webhook_url_detection import lan_ip_candidate

        req = MagicMock()
        req.headers = {"host": "192.168.1.50:8085"}
        assert (
            lan_ip_candidate(req, 8085)
            == "http://192.168.1.50:8085/api/webhooks/plex"
        )

    def test_strips_port_from_host(self):
        from app.services.webhook_url_detection import lan_ip_candidate

        req = MagicMock()
        req.headers = {"host": "myhost:9999"}
        assert lan_ip_candidate(req, 8085) == "http://myhost:8085/api/webhooks/plex"

    def test_returns_none_when_no_host_header(self):
        from app.services.webhook_url_detection import lan_ip_candidate

        req = MagicMock()
        req.headers = {}
        assert lan_ip_candidate(req, 8085) is None


class TestTailscaleCandidate:
    @patch("app.services.webhook_url_detection.psutil")
    def test_finds_100_x_address(self, mock_psutil):
        addr = MagicMock()
        addr.family = socket.AF_INET
        addr.address = "100.92.42.5"
        mock_psutil.net_if_addrs.return_value = {"tailscale0": [addr]}
        from app.services.webhook_url_detection import tailscale_candidate

        assert (
            tailscale_candidate(8085)
            == "http://100.92.42.5:8085/api/webhooks/plex"
        )

    @patch("app.services.webhook_url_detection.psutil")
    def test_returns_none_when_no_100_x(self, mock_psutil):
        addr = MagicMock()
        addr.family = socket.AF_INET
        addr.address = "192.168.1.50"
        mock_psutil.net_if_addrs.return_value = {"eth0": [addr]}
        from app.services.webhook_url_detection import tailscale_candidate

        assert tailscale_candidate(8085) is None

    @patch("app.services.webhook_url_detection.psutil", None)
    def test_returns_none_when_psutil_unavailable(self):
        from app.services.webhook_url_detection import tailscale_candidate

        assert tailscale_candidate(8085) is None

    @patch("app.services.webhook_url_detection.psutil")
    def test_skips_non_ipv4_families(self, mock_psutil):
        addr_v6 = MagicMock()
        addr_v6.family = socket.AF_INET6
        addr_v6.address = "fd7a:115c:a1e0::5"  # would otherwise look "ts-ish"
        mock_psutil.net_if_addrs.return_value = {"tailscale0": [addr_v6]}
        from app.services.webhook_url_detection import tailscale_candidate

        assert tailscale_candidate(8085) is None


class TestGetWebhookUrlCandidates:
    @patch("app.services.webhook_url_detection.tailscale_candidate")
    @patch("app.services.webhook_url_detection.lan_ip_candidate")
    @patch("app.services.webhook_url_detection.docker_hostname_candidate")
    def test_returns_dicts_with_label_and_url(self, mock_docker, mock_lan, mock_ts):
        mock_docker.return_value = "http://composer:8085/api/webhooks/plex"
        mock_lan.return_value = "http://192.168.1.50:8085/api/webhooks/plex"
        mock_ts.return_value = None  # no Tailscale on this test rig
        from app.services.webhook_url_detection import get_webhook_url_candidates

        req = MagicMock()
        cands = get_webhook_url_candidates(req, port=8085)

        assert len(cands) == 2  # None ones filtered out
        assert all("label" in c and "url" in c for c in cands)

    @patch("app.services.webhook_url_detection.tailscale_candidate")
    @patch("app.services.webhook_url_detection.lan_ip_candidate")
    @patch("app.services.webhook_url_detection.docker_hostname_candidate")
    def test_returns_three_when_all_present(self, mock_docker, mock_lan, mock_ts):
        mock_docker.return_value = "http://composer:8085/api/webhooks/plex"
        mock_lan.return_value = "http://192.168.1.50:8085/api/webhooks/plex"
        mock_ts.return_value = "http://100.92.42.5:8085/api/webhooks/plex"
        from app.services.webhook_url_detection import get_webhook_url_candidates

        req = MagicMock()
        cands = get_webhook_url_candidates(req, port=8085)
        assert len(cands) == 3
        urls = [c["url"] for c in cands]
        assert "http://composer:8085/api/webhooks/plex" in urls
        assert "http://192.168.1.50:8085/api/webhooks/plex" in urls
        assert "http://100.92.42.5:8085/api/webhooks/plex" in urls

    @patch("app.services.webhook_url_detection.tailscale_candidate")
    @patch("app.services.webhook_url_detection.lan_ip_candidate")
    @patch("app.services.webhook_url_detection.docker_hostname_candidate")
    def test_returns_empty_when_all_none(self, mock_docker, mock_lan, mock_ts):
        mock_docker.return_value = None
        mock_lan.return_value = None
        mock_ts.return_value = None
        from app.services.webhook_url_detection import get_webhook_url_candidates

        req = MagicMock()
        cands = get_webhook_url_candidates(req, port=8085)
        assert cands == []
