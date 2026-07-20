"""Shared lifecycle helpers: a shutdown flag, and an exact-time reminder
scheduler (threading.Timer → fires to the second)."""

from __future__ import annotations

import threading
from typing import Callable, Optional

_shutdown = threading.Event()
_reminder_sink: Optional[Callable[[str], None]] = None


# --- shutdown --------------------------------------------------------------
def request_shutdown() -> None:
    _shutdown.set()


def shutdown_requested() -> bool:
    return _shutdown.is_set()


# --- exact reminders / timers ---------------------------------------------
def register_reminder_sink(cb: Callable[[str], None]) -> None:
    """The engine registers how a due reminder should be announced."""
    global _reminder_sink
    _reminder_sink = cb


def schedule_reminder(seconds: float, text: str) -> None:
    """Fire `text` exactly `seconds` from now (to the second)."""
    def fire() -> None:
        try:
            from jarvis import notices
            notices.add("reminder", text, "interrupt")  # recorded / held if away
        except Exception:
            pass
        if _reminder_sink:
            _reminder_sink(text)

    timer = threading.Timer(max(0.0, seconds), fire)
    timer.daemon = True
    timer.start()
