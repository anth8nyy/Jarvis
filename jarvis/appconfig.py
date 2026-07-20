"""Non-secret app configuration, loaded from config.json at the project root.

Secrets live in .env (config.py). This is the human-editable knobs file:
quiet hours, heartbeat interval, which checks run and how often. Tuning
behavior is a one-line edit here, never a code change. Tier 6 extends this
same file with confirmation and model settings.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, time
from typing import Any, Dict

_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")

_DEFAULTS: Dict[str, Any] = {
    "quiet_hours": {"start": "22:00", "end": "08:00"},
    "heartbeat": {
        "tick_seconds": 15,
        "checks": {
            "due_reminders": {"every_seconds": 30, "enabled": True},
        },
    },
    # Tools that must get an explicit yes before running (Tier 6 gate). Adds to
    # any tool already flagged requires_confirmation in code.
    "confirm_tools": ["delete_task", "forget_fact"],
    # For the model-cost tally. Sonnet 5 intro rates (through 2026-08-31);
    # edit to match your model/plan.
    "pricing": {"input_per_mtok": 2.0, "output_per_mtok": 10.0},
}


def _deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base)
    for key, value in over.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load() -> Dict[str, Any]:
    try:
        with open(_PATH, "r") as fh:
            user = json.load(fh)
    except FileNotFoundError:
        user = {}
    return _deep_merge(_DEFAULTS, user)


def _parse_hhmm(text: str) -> time:
    hour, minute = text.split(":")
    return time(int(hour), int(minute))


def in_quiet_hours(now: datetime | None = None) -> bool:
    """True if the current time falls in the configured quiet window.

    Handles windows that wrap past midnight (e.g. 22:00–08:00).
    """
    cfg = load()["quiet_hours"]
    start = _parse_hhmm(cfg["start"])
    end = _parse_hhmm(cfg["end"])
    t = (now or datetime.now()).time()
    if start <= end:
        return start <= t < end
    return t >= start or t < end
