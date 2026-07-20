"""The single place proactive items land — durable and dismissible.

A notice the heartbeat raises while the user is away must be *held*, not
fired into the void: it lives in data/notices.json until the user dismisses
it. That's what makes proactivity catch-up-on-return instead of
deliver-once-and-lose.

Levels:
  "calm"      — accumulates quietly; shown when the user chooses to look.
  "interrupt" — worth surfacing now (subject to quiet hours), but still held.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List

from jarvis.registry import Registry, Tool

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
_PATH = os.path.join(_DATA_DIR, "notices.json")


def _load() -> List[Dict[str, Any]]:
    try:
        with open(_PATH, "r") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return []


def _save(notices: List[Dict[str, Any]]) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(_PATH, "w") as fh:
        json.dump(notices, fh, indent=2)


def _next_id(notices: List[Dict[str, Any]]) -> int:
    return max((n["id"] for n in notices), default=0) + 1


def add(source: str, text: str, level: str = "calm") -> Dict[str, Any]:
    notices = _load()
    notice = {
        "id": _next_id(notices),
        "source": source,
        "text": text,
        "level": level,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "dismissed": False,
    }
    notices.append(notice)
    _save(notices)
    return notice


def pending() -> List[Dict[str, Any]]:
    return [n for n in _load() if not n["dismissed"]]


def dismiss(id: int) -> str:
    notices = _load()
    for notice in notices:
        if notice["id"] == id:
            notice["dismissed"] = True
            _save(notices)
            return f"Dismissed notice #{id}."
    return f"No notice with id #{id}."


def render_pending() -> str:
    items = pending()
    if not items:
        return "Nothing waiting for you."
    lines = [f"#{n['id']} ({n['created_at']}) {n['text']}" for n in items]
    return "\n".join(lines)


def register(registry: Registry) -> None:
    registry.register(
        Tool(
            name="list_notices",
            description="List the proactive notices waiting for the user (reminders and things Jarvis surfaced while they were away).",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=lambda: render_pending(),
        )
    )
    registry.register(
        Tool(
            name="dismiss_notice",
            description="Dismiss/acknowledge a surfaced notice by its id number so it clears from the waiting list.",
            input_schema={
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "The notice's id number."}
                },
                "required": ["id"],
            },
            handler=dismiss,
        )
    )
