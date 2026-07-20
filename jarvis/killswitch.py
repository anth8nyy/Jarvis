"""The kill switch — one obvious way to halt all proactive behavior at once.

Backed by a flag file so it survives restarts: flip it on and the heartbeat
stops surfacing anything, on this run and every future one, until you flip it
off. The conversation loop keeps working — you can still talk to Jarvis.
"""

from __future__ import annotations

import os

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
_FLAG = os.path.join(_DATA_DIR, "paused.flag")


def pause() -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    open(_FLAG, "w").close()


def resume() -> None:
    try:
        os.remove(_FLAG)
    except FileNotFoundError:
        pass


def is_paused() -> bool:
    return os.path.exists(_FLAG)
