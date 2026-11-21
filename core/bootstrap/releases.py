"""The release catalogue behind the version picker.

One cached read of GitHub's releases API gives every published cloudflared
build — including pre-releases — with its publish time. Nothing here touches
the UI or the process: the payload layer turns the catalogue into display rows,
and ``resolved_version`` only needs the ordered list of tags.

The API is called at most once per process (three pages, no browser access).
If GitHub is unreachable the built-in VERSION is still offered, so a cold boot
never leaves the picker empty.
"""
from __future__ import annotations

import functools
import json
import re
import time
import urllib.request

RELEASES_API = (
    "https://api.github.com/repos/cloudflare/cloudflared/releases"
    "?per_page=100&page={page}"
)
RELEASE_TIMEOUT = 6.0
MAX_PAGES = 3
STABLE_TAG = re.compile(r"^[0-9]{4}\.[0-9]{1,2}\.[0-9]{1,2}$")
# What may be written to the version file: a release tag, optionally with a
# pre-release suffix. No spaces, slashes or shell characters ever reach disk.
VALID_TAG = re.compile(r"^[0-9]{4}\.[0-9]{1,2}\.[0-9]{1,2}(?:-[A-Za-z0-9.-]+)?$")
_EPOCH = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T")


def age_text(published: float, now: float | None = None) -> str:
    """A short relative age: '3 days ago', '2 weeks ago', 'just now'."""
    if published <= 0:
        return ""
    seconds = max(0, int((now if now is not None else time.time()) - published))
    if seconds < 60:
        return "just now"
    units = (
        (365 * 86400, "year"), (30 * 86400, "month"),
        (7 * 86400, "week"), (86400, "day"), (3600, "hour"),
        (60, "minute"), (0, "second"),
    )
    limit, unit = next((size, name) for size, name in units if seconds >= size)
    count = seconds // limit
    return f"{count} {unit}{'s' if count != 1 else ''} ago"


def _published_at(value: object) -> float:
    """GitHub's ISO timestamp to epoch, without importing dateutil."""
    match = _EPOCH.match(str(value or ""))
    if not match:
        return 0.0
    year, month, day = (int(match.group(i)) for i in (1, 2, 3))
    if not (1970 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31):
        return 0.0
    return time.mktime((year, month, day, 0, 0, 0, 0, 0, -1))


@functools.lru_cache(maxsize=1)
def _catalog() -> tuple[tuple[str, float, bool], ...]:
    found: list[tuple[str, float, bool]] = []
    seen: set[str] = set()
    for page in range(1, MAX_PAGES + 1):
        request = urllib.request.Request(
            RELEASES_API.format(page=page),
            headers={"User-Agent": "apllme-card",
                     "Accept": "application/vnd.github+json"})
        try:
            with urllib.request.urlopen(request, timeout=RELEASE_TIMEOUT) as reply:
                items = json.loads(reply.read())
        except Exception:  # noqa: BLE001 - a blocked page must not kill the feed
            break
        if not isinstance(items, list) or not items:
            break
        for item in items:
            if not isinstance(item, dict) or item.get("draft"):
                continue
            tag = str(item.get("tag_name") or "").strip()
            if not tag or tag in seen:
                continue
            seen.add(tag)
            # A non-calendar tag is a pre-release even when GitHub forgot to
            # flag it: every published build stays in the list.
            pre = bool(item.get("prerelease")) or not bool(STABLE_TAG.fullmatch(tag))
            found.append((tag, _published_at(item.get("published_at")), pre))
    if not found:
        # The only local fallback is the release this build was pinned to.
        from core.bootstrap.config import VERSION
        return ((VERSION, 0.0, False),)
    return tuple(found)


def tags() -> list[str]:
    """Ordered tags, newest first — what ``resolved_version`` needs."""
    return [tag for tag, _, _ in _catalog()]


def catalogue(now: float | None = None) -> list[dict[str, object]]:
    """Display rows: tag, relative age and the pre-release flag."""
    return [{"tag": tag, "age": age_text(published, now), "pre": pre}
            for tag, published, pre in _catalog()]


def versions() -> list[str]:
    """Back-compatible name for the ordered tag list."""
    return tags()


versions.cache_clear = _catalog.cache_clear
catalogue.cache_clear = _catalog.cache_clear
