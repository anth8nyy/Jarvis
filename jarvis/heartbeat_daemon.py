"""Standalone heartbeat: `python -m jarvis --heartbeat`.

This is what runs on the always-on server (per AGENT.md). It beats on its own,
surfacing due notices to the durable store and printing interrupt-level ones
to stdout. The conversation loop connects to the same data/ store to show and
dismiss what it raises.
"""

from __future__ import annotations

import time

from jarvis.heartbeat import Heartbeat


def run() -> None:
    def announce(notice: dict) -> None:
        print(f"\n🔔 {notice['text']}  (notice #{notice['id']})")

    hb = Heartbeat(on_interrupt=announce)
    hb.start()
    print("Heartbeat running. Ctrl-C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        hb.stop()
        print("\nHeartbeat stopped.")
