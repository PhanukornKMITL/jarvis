"""Latest Thai news through Google News RSS (its public feed, searchable), for what happened
hours ago, like a flash flood, that forecasts and satellites don't show. Outlets' own feeds
hold only their last 20-50 items, a few hours: the Pluak Daeng flood news from the day
before was already gone. Only headline, outlet and time are passed on, so JARVIS can say
who reported it and when.
"""

from __future__ import annotations

import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape

SEARCH = "https://news.google.com/rss/search?q={query}+when:2d&hl=th&gl=TH&ceid=TH:th"
TOP = "https://news.google.com/rss?hl=th&gl=TH&ceid=TH:th"
CACHE_SECONDS = 600
MAX_ITEMS = 5
_cache: dict[str, tuple[float, list[str]]] = {}
# Parsed with regexes, not xml.etree: this Mac's Homebrew Python 3.14 has a broken pyexpat.
_ITEM = re.compile(r"<item\b.*?</item>", re.S)


def _field(item: str, tag: str) -> str:
    match = re.search(rf"<{tag}\b[^>]*>(?:\s*<!\[CDATA\[)?(.*?)(?:\]\]>\s*)?</{tag}>", item, re.S)
    return unescape(match.group(1)).strip() if match else ""


def _ago(published: datetime) -> str:
    hours = (datetime.now(timezone.utc) - published).total_seconds() / 3600
    return f"{max(1, round(hours * 60))} นาทีก่อน" if hours < 1 else f"{round(hours)} ชั่วโมงก่อน"


def headlines(query: str = "") -> list[str]:
    """Newest first: "ไทยรัฐ 3 ชั่วโมงก่อน: <headline>". Empty query = top Thai news."""
    url = SEARCH.format(query=urllib.parse.quote_plus(query)) if query else TOP
    cached = _cache.get(url)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]
    request = urllib.request.Request(url, headers={"User-Agent": "JARVIS-home-assistant/0.1 (personal)"})
    with urllib.request.urlopen(request, timeout=10) as response:
        feed = response.read().decode("utf-8", errors="replace")
    items = []
    for item in _ITEM.findall(feed):
        try:
            published = parsedate_to_datetime(_field(item, "pubDate"))
        except (TypeError, ValueError):
            continue
        outlet = _field(item, "source")
        title = _field(item, "title").removesuffix(f" - {outlet}")  # Google appends the outlet
        items.append((published, f"{outlet} {_ago(published)}: {title}"))
    items.sort(reverse=True)
    lines = [line for _, line in items[:MAX_ITEMS]]
    _cache[url] = (time.monotonic(), lines)
    return lines
