"""Geo facts for the card, resolved once per IP and cached in the process.

The browser never calls a third-party API: a card inside a sandboxed viewer
frame has no reliable network of its own, and an IP lookup on every poll would
be slow and rude. The sampler resolves the origin/edge IPs in its background
thread and publishes ISO country, region and city in state.json. The card only
picks the matching local SVG flag from ``static/flags/``.

Source: https://ipwho.is (free HTTPS JSON, ~0.1 s from the Streamlit container).
No key, no account, no PII beyond the IP that already appears on the card.
"""
from __future__ import annotations

import functools
import ipaddress
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

API = "https://ipwho.is/{ip}?fields=success,country,country_code,region,region_code,city"
TIMEOUT = 1.5
CACHE: dict[str, dict[str, str]] = {}
RESOLV = Path("/etc/resolv.conf")


def _fetch(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=TIMEOUT) as reply:
        return json.loads(reply.read(4096).decode("utf-8"))


def lookup(ip: str) -> dict[str, str]:
    """Country/region for one public IP; {} for private, bad or offline IPs."""
    if not ip or ip == "—":
        return {}
    try:
        address = ipaddress.ip_address(ip)
        if not address.is_global:
            return {}
    except ValueError:
        return {}
    if ip in CACHE:
        return CACHE[ip]
    result: dict[str, str] = {}
    try:
        data = _fetch(API.format(ip=urllib.parse.quote(ip)))
        if data.get("success"):
            cc = str(data.get("country_code") or "").upper()
            if re.fullmatch(r"[A-Z]{2}", cc):
                result = {
                    "cc": cc,
                    "country": str(data.get("country") or ""),
                    "region": str(data.get("region") or ""),
                    "region_code": str(data.get("region_code") or ""),
                    "city": str(data.get("city") or ""),
                }
    except Exception:  # noqa: BLE001 - a geo miss must never break the feed
        result = {}
    CACHE[ip] = result
    return result


@functools.lru_cache(maxsize=1)
def placement() -> str:
    """Cloud placement from the platform's own resolv.conf search domain.

    Streamlit Cloud does not expose a numeric "shard" in /api/v2/app/status or
    in the environment. The only placement identifier it does expose is the
    Kubernetes search domain, e.g. ``us-west1-b.c.s4a-prod.internal``. We show
    that verbatim as cluster/zone instead of inventing a shard number.
    """
    try:
        text = RESOLV.read_text(encoding="ascii")
    except OSError:
        return ""
    for line in text.splitlines():
        if not line.strip().startswith("search "):
            continue
        for token in line.split()[1:]:
            match = re.fullmatch(r"([a-z0-9-]+)\.c\.([a-z0-9-]+)\.internal", token)
            if match:
                zone, cluster = match.groups()
                return f"{cluster}/{zone}"
    return ""
