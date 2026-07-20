"""Scheduled checks — the things the heartbeat looks at.

Each check is a small, self-contained unit: a function that inspects some
state and returns a list of (text, level) items worth surfacing — or an empty
list, which is the common case. Quiet by default: a check that finds nothing
returns nothing, and nothing reaches the user.

Adding a proactive behavior means writing one function and registering it
here, plus a line in config.json for how often it runs. The heartbeat loop
never changes.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Tuple

from jarvis.tools import tasks

# A check returns a list of (text, level) pairs. level is "calm" or "interrupt".
CheckResult = List[Tuple[str, str]]
CheckFn = Callable[[], CheckResult]

_CHECKS: Dict[str, CheckFn] = {}


def register(name: str, fn: CheckFn) -> None:
    _CHECKS[name] = fn


def run(name: str) -> CheckResult:
    fn = _CHECKS.get(name)
    if fn is None:
        return []
    return fn()


def _due_reminders() -> CheckResult:
    """Surface reminders that have come due. The user explicitly asked to be
    reminded, so these are interrupt-level."""
    return [(f"⏰ Reminder: {text}", "interrupt") for text in tasks.pop_due_reminders()]


register("due_reminders", _due_reminders)
