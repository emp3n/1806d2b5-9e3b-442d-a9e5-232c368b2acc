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
# Our own egress IP and region. The connector's trace has no edge IP while the
# tunnel is stopped, but the Origin chip must stay honest: the machine is still
# here. One cached call, on the server, so the browser never sees a third party.
API_OWN = "https://ipwho.is/?fields=ip,success,country,country_code,region,region_code,city"
# api.ip.sb answers 403 from the Streamlit container, so the own-IP lookup uses
# the same provider as the geo lookups and falls back to a second one. All of
# them are server-side and cached for the life of the process.
OWN_FALLBACKS = ("https://ipapi.co/json/", "https://ipinfo.io/json")
TIMEOUT = 1.5
CACHE: dict[str, dict[str, str]] = {}
OWN_CACHE: dict[str, tuple[str, dict[str, str]]] = {}
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


def own_endpoint() -> tuple[str, dict[str, str]]:
    """Our public IP and its region, resolved once and cached.

    The Origin chip shows the connector's edge IP while cloudflared runs and
    this value while it is stopped; either way it is a real address with a real
    place, never a decorative dash. The lookup is server-side only — a poll
    must never call a third party. Several resolvers are tried because some
    block cloud datacentre IPs (api.ip.sb answers 403 from Streamlit Cloud).
    """
    if "value" in OWN_CACHE:
        return OWN_CACHE["value"]
    result: tuple[str, dict[str, str]] = ("", {})
    for url in (API_OWN,) + OWN_FALLBACKS:
        try:
            data = _fetch(url)
        except Exception:  # noqa: BLE001 - try the next resolver
            continue
        candidate = str(data.get("ip") or "")
        try:
            if not ipaddress.ip_address(candidate).is_global:
                continue
        except ValueError:  # a resolver may answer with a hostname or junk
            continue
        cc = str(data.get("country_code") or "").upper()
        info: dict[str, str] = {}
        if re.fullmatch(r"[A-Z]{2}", cc):
            info = {
                "cc": cc,
                "country": str(data.get("country_name") or data.get("country") or ""),
                "region": str(data.get("region") or ""),
                "city": str(data.get("city") or ""),
            }
        result = (candidate, info)
        break
    OWN_CACHE["value"] = result
    return result


API_APP = "{base}/api/v2/app/disambiguate"


@functools.lru_cache(maxsize=4)
def _shard_cached(app_url: str) -> str:
    """Cluster id from Streamlit's own disambiguate endpoint, e.g. shard-3.

    /api/v2/app/status and the container env do not carry the cluster. The
    public disambiguate route does; it is read-only and the answer is cached.
    """
    if not app_url:
        return ""
    try:
        data = _fetch(API_APP.format(base=app_url))
        value = str(data.get("cluster") or "")
        return value if re.fullmatch(r"shard-[A-Za-z0-9-]+", value) else ""
    except Exception:  # noqa: BLE001 - a missing shard must not break the feed
        return ""


def shard(app_url: str) -> str:
    """Normalize the URL before the cache key: / and no-/ are one app."""
    return _shard_cached((app_url or "").rstrip("/"))


shard.cache_clear = _shard_cached.cache_clear
