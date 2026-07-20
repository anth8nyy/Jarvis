"""Web lookups — a DuckDuckGo instant-answer for facts, plus news search.
Free, no API key."""

from __future__ import annotations

import requests

from jarvis import news
from jarvis.registry import Registry, Tool

_DDG = "https://api.duckduckgo.com/"
_HEADERS = {"User-Agent": "Mozilla/5.0"}


def web_search(query: str) -> str:
    # 1) DuckDuckGo instant answer (good for facts/definitions).
    try:
        r = requests.get(
            _DDG,
            params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
            timeout=10,
            headers=_HEADERS,
        )
        data = r.json()
        abstract = data.get("AbstractText") or data.get("Answer")
        if abstract:
            return abstract
        topics = data.get("RelatedTopics", [])
        blurbs = [t.get("Text") for t in topics if isinstance(t, dict) and t.get("Text")]
        if blurbs:
            return " ".join(blurbs[:2])
    except Exception:
        pass
    # 2) Fall back to news/web headlines for the query.
    hs = news.headlines(3, query=query)
    if hs:
        return "Here's what I found: " + "; ".join(hs)
    return f"I couldn't find anything solid on '{query}', sir."


def get_news(topic: str = "") -> str:
    hs = news.headlines(6, query=topic or None)
    if not hs:
        return "I couldn't reach the news right now, sir."
    label = f"news on {topic}" if topic else "today's top stories"
    return f"Here's {label}, sir: " + ". ".join(f"{i}. {h}" for i, h in enumerate(hs, 1))


def register(registry: Registry) -> None:
    registry.register(
        Tool(
            name="web_search",
            description="Search the web for current facts, prices, events, or any question needing up-to-date info. Use whenever you don't know something or it may have changed.",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string", "description": "What to look up."}},
                "required": ["query"],
            },
            handler=web_search,
        )
    )
    registry.register(
        Tool(
            name="get_news",
            description="Get today's news headlines. Optionally about a specific topic (e.g. 'technology', 'Greece', 'football'). Use when the user asks for the news or what's happening.",
            input_schema={
                "type": "object",
                "properties": {"topic": {"type": "string", "description": "Optional topic; omit for top stories."}},
                "required": [],
            },
            handler=get_news,
        )
    )
