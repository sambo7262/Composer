"""Phase 5 webhook URL auto-detection (EVT-07 / D-12).

Returns up to 3 candidates: Docker hostname, LAN IP from request Host header,
Tailscale 100.x. The user picks whichever turns ✓ first via the wizard's
Test webhook flow.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Dict, List, Optional

from fastapi import Request

try:
    import psutil  # in requirements.txt
except ImportError:  # pragma: no cover — defensive only
    psutil = None  # type: ignore[assignment]

# CGNAT / RFC 6598 — Tailscale issues 100.64.0.0/10 by default.
TAILSCALE_RANGE = ipaddress.IPv4Network("100.64.0.0/10")


def docker_hostname_candidate(port: int) -> Optional[str]:
    """E.g. ``http://composer:8085/api/webhooks/plex`` when Plex shares synobridge.

    Returns None if the hostname is unset, ``localhost``, or the loopback IP.
    """
    try:
        host = socket.gethostname()
    except OSError:
        return None
    if not host or host in ("localhost", "127.0.0.1"):
        return None
    return f"http://{host}:{port}/api/webhooks/plex"


def lan_ip_candidate(request: Request, port: int) -> Optional[str]:
    """Use the Host header from the user's first browser request.

    That's the IP/hostname the user actually reaches Composer at. The port is
    overridden to the configured webhook port — Plex calls Composer, not the
    user's browser, so the user's browser port is irrelevant.
    """
    host_header = request.headers.get("host", "")
    if not host_header:
        return None
    host_only = host_header.split(":")[0]
    if not host_only:
        return None
    return f"http://{host_only}:{port}/api/webhooks/plex"


def tailscale_candidate(port: int) -> Optional[str]:
    """Find any 100.64.0.0/10 IPv4 bound on this container."""
    if psutil is None:
        return None
    for _iface_name, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family != socket.AF_INET:
                continue
            try:
                ip = ipaddress.IPv4Address(addr.address)
            except (ipaddress.AddressValueError, ValueError):
                continue
            if ip in TAILSCALE_RANGE:
                return f"http://{ip}:{port}/api/webhooks/plex"
    return None


def get_webhook_url_candidates(
    request: Request, port: int = 8085
) -> List[Dict[str, str]]:
    """Return up to 3 candidate dicts with ``label`` and ``url`` keys.

    None entries are filtered. Order is the priority order users should try:
    Docker hostname (cheapest, may not work if Plex is on host network),
    LAN IP (most likely winner on the NAS deployment), Tailscale (fallback
    for remote access).
    """
    out: List[Dict[str, str]] = []
    d = docker_hostname_candidate(port)
    if d:
        out.append({"label": "Docker hostname (synobridge)", "url": d})
    lan = lan_ip_candidate(request, port)
    if lan:
        out.append({"label": "LAN / NAS IP", "url": lan})
    ts = tailscale_candidate(port)
    if ts:
        out.append({"label": "Tailscale (remote access)", "url": ts})
    return out
