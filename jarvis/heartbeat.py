"""The heartbeat — Jarvis acting without being spoken to.

A background loop, separate from the conversation loop, that wakes on an
interval, runs any checks that are due, and routes what they surface into the
durable notices store. Designed to be relocatable: it doesn't care whether
it's running on the laptop or an always-on server (per AGENT.md, the server
is its eventual home).

The hard-won discipline is baked in:
  - Quiet by default: checks usually return nothing.
  - Held, not dropped: everything goes to the durable notices store first.
  - Quiet hours: interrupt-level delivery waits for waking hours; the notice
    is still stored so it's not lost.
  - Restart-safe: next-due per check is persisted, so restarting doesn't
    reset every timer or fire everything at once on boot.
  - No pile-ups: a check still running when its next turn comes due is
    skipped, not stacked.
  - Pausable: a single flag halts all proactive behavior (the Tier 6 kill
    switch flips this) while the conversation loop keeps working.
"""

from __future__ import annotations

import json
import os
import threading
import time as _time
from datetime import datetime, timedelta
from typing import Callable, Dict, Optional

from jarvis import appconfig, checks, killswitch, notices

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
_SCHEDULE_PATH = os.path.join(_DATA_DIR, "schedule.json")


def _load_schedule() -> Dict[str, str]:
    try:
        with open(_SCHEDULE_PATH, "r") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}


def _save_schedule(schedule: Dict[str, str]) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(_SCHEDULE_PATH, "w") as fh:
        json.dump(schedule, fh, indent=2)


class Heartbeat:
    def __init__(self, on_interrupt: Optional[Callable[[dict], None]] = None):
        # Called when an interrupt-level notice should be delivered live (and
        # we're not in quiet hours). Optional — the notice is stored regardless.
        self._on_interrupt = on_interrupt
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._running_checks: set[str] = set()
        self._thread: Optional[threading.Thread] = None

    # --- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    # --- the loop ----------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            # Respect both the in-memory pause and the durable kill switch
            # (a paused.flag file that survives restarts).
            if not self._paused.is_set() and not killswitch.is_paused():
                try:
                    self.tick()
                except Exception as exc:  # a check blowing up must not kill the loop
                    print(f"[heartbeat error: {exc}]")
            tick_seconds = appconfig.load()["heartbeat"]["tick_seconds"]
            self._stop.wait(tick_seconds)

    def tick(self, now: Optional[datetime] = None) -> None:
        now = now or datetime.now()
        cfg = appconfig.load()["heartbeat"]["checks"]
        schedule = _load_schedule()
        changed = False

        for name, check_cfg in cfg.items():
            if not check_cfg.get("enabled", True):
                continue
            every = timedelta(seconds=check_cfg["every_seconds"])

            due_at = schedule.get(name)
            if due_at is None:
                # First time we've seen this check: schedule it forward, don't
                # fire on boot (avoids a startup storm).
                schedule[name] = (now + every).isoformat(timespec="seconds")
                changed = True
                continue

            if now < datetime.fromisoformat(due_at):
                continue  # not due yet

            if name in self._running_checks:
                continue  # still running from a previous tick — skip, don't stack

            self._running_checks.add(name)
            try:
                results = checks.run(name)
            finally:
                self._running_checks.discard(name)

            for text, level in results:
                self._surface(name, text, level, now)

            # Reschedule from now (catch-up after downtime fires once, not N times).
            schedule[name] = (now + every).isoformat(timespec="seconds")
            changed = True

        if changed:
            _save_schedule(schedule)

    def _surface(self, source: str, text: str, level: str, now: datetime) -> None:
        # Always store first — held, never dropped.
        notice = notices.add(source, text, level)
        # Deliver live only if it's an interrupt, we're outside quiet hours,
        # and someone's listening. Otherwise it waits in the notices store.
        if (
            level == "interrupt"
            and not appconfig.in_quiet_hours(now)
            and self._on_interrupt is not None
        ):
            self._on_interrupt(notice)
