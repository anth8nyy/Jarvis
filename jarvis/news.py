"""Today's news headlines via Google News RSS (free, no API key)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import requests

_TOP = "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"
_SEARCH = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
_HEADERS = {"User-Agent": "Mozilla/5.0"}


def _clean(title: str) -> str:
    # Google News titles end with " - Source"; drop the trailing source.
    return re.sub(r"\s+-\s+[^-]+$", "", title).strip()


def headlines(count: int = 6, query: str | None = None) -> list[str]:
    url = _SEARCH.format(q=requests.utils.quote(query)) if query else _TOP
    try:
        r = requests.get(url, timeout=10, headers=_HEADERS)
        root = ET.fromstring(r.content)
    except Exception:
        return []
    out, seen = [], set()
    for item in root.findall(".//item"):
        title = _clean(item.findtext("title", ""))
        if title and title.lower() not in seen:
            seen.add(title.lower())
            out.append(title)
        if len(out) >= count:
            break
    return out


def briefing(count: int = 6) -> str:
    """A spoken-friendly rundown of today's top headlines."""
    hs = headlines(count)
    if not hs:
        return "I couldn't reach the news right now, sir."
    lines = ". ".join(f"{i}. {h}" for i, h in enumerate(hs, 1))
    return f"Here are today's top stories, sir. {lines}."
