"""Always-on global-hotkey daemon.

A tiny separate process kept alive by launchd, so the shortcuts keep working
even when the Jarvis engine itself has quit:

  ⌃⌥J  show Jarvis's window — LAUNCHING the app first if it isn't running
  ⌃⌥Q  quit the running Jarvis completely (no-op if he's not running)

It runs through the same Jarvis.app binary as the engine (JARVIS_MODE=hotkeyd),
so the one Accessibility grant for "Jarvis" covers both. pynput needs that
permission; until it's granted it logs "not trusted" and keys do nothing.
"""

from __future__ import annotations

import os
import subprocess
import urllib.request

_DATA = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
_LOCK = os.path.join(_DATA, "jarvis.pid")
_URLFILE = os.path.join(_DATA, "jarvis.url")
_APP = os.path.expanduser("~/Desktop/Jarvis.app")


def _engine_url() -> str | None:
    """The running engine's local URL, or None if it isn't alive."""
    try:
        with open(_LOCK) as fh:
            pid = int(fh.read().strip())
        os.kill(pid, 0)  # raises if dead
        return open(_URLFILE).read().strip()
    except Exception:
        return None


def _post(url: str, path: str) -> bool:
    try:
        urllib.request.urlopen(url + path, data=b"", timeout=2)
        return True
    except Exception:
        return False


def _open_jarvis() -> None:
    """⌃⌥J: summon the window; launch the whole app if it's not running."""
    url = _engine_url()
    if url and _post(url, "show"):
        return
    subprocess.Popen(["open", _APP])


def _quit_jarvis() -> None:
    """⌃⌥Q: ask the running engine to shut down completely."""
    url = _engine_url()
    if url:
        _post(url, "quitall")


def run() -> None:
    from pynput import keyboard

    print("[hotkeyd] ⌃⌥J open · ⌃⌥Q quit — standing by", flush=True)
    with keyboard.GlobalHotKeys({
        "<ctrl>+<alt>+j": _open_jarvis,
        "<ctrl>+<alt>+q": _quit_jarvis,
    }) as hk:
        hk.join()  # blocks forever; launchd restarts us if we ever die
