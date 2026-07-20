"""Multi-step voice routines — choreographed sequences a single tool can't do.

start_day ("Let's start the day, Jarvis"): open Spotify and play the first 11
seconds of Should I Stay or Should I Go, while Claude, Notes and ChatGPT open
in parallel, then quit Spotify. Interruptible: a barge-in (the `cancel` event)
skips straight to the cleanup.
"""

from __future__ import annotations

import subprocess
import threading
import time

from jarvis.tools.mac import close_app, open_app

_SONG = "Should I Stay or Should I Go The Clash"
_APPS = (("Claude", None), ("Notes", None))
_PLAY_SECONDS = 11.0   # stop when SPOTIFY'S OWN playhead reaches this

# Runs inside one osascript process: watches Spotify's player position (the
# song's real clock — no Python timers) and pauses the moment it crosses the
# mark. Checks every ~20ms; the iteration cap is a safety net (~60s). The
# position read sits in a `try` because it errors briefly while a freshly
# launched Spotify is still loading the track — that must not kill the watch.
_WATCH_SCRIPT = f'''
tell application "Spotify"
    set n to 0
    repeat
        try
            if (player position) ≥ {_PLAY_SECONDS} then exit repeat
        end try
        delay 0.02
        set n to n + 1
        if n > 2000 then exit repeat
    end repeat
    pause
end tell
'''
_NOTES_FOLDER = "Business"
# The Claude desktop app ignores plain URLs and exposes no accessibility tree,
# but it DOES honour its own claude://claude.ai/<path> scheme. "epitaxy" is the
# Claude Code route (verified: it takes the app off Claude Home onto Claude
# Code, landing on the active session).
_CLAUDE_CODE_URL = "claude://claude.ai/epitaxy"


def _open_claude_code() -> str:
    """Open Claude straight onto Claude Code rather than Claude Home."""
    open_app("Claude")
    r = subprocess.run(["open", _CLAUDE_CODE_URL], capture_output=True, text=True)
    return "Claude on Claude Code" if r.returncode == 0 else "Claude"


def _open_notes_folder() -> str:
    """Open Notes straight onto the Business folder (falls back to just
    opening Notes if the folder ever disappears)."""
    r = subprocess.run(
        ["osascript", "-e",
         f'tell application "Notes"\nactivate\nshow folder "{_NOTES_FOLDER}"\nend tell'],
        capture_output=True, text=True, timeout=15,
    )
    if r.returncode == 0:
        return f"Notes on {_NOTES_FOLDER}"
    open_app("Notes")
    return "Notes"


def _track_uri(query: str) -> str:
    """Spotify URI for the top search hit. Client-credentials flow — search
    needs no user login, so this never blocks on a browser prompt."""
    import spotipy
    from spotipy.oauth2 import SpotifyClientCredentials

    from jarvis import config

    if not config.SPOTIFY_CLIENT_ID or not config.SPOTIFY_CLIENT_SECRET:
        raise RuntimeError("Spotify credentials aren't set")
    sp = spotipy.Spotify(
        auth_manager=SpotifyClientCredentials(
            client_id=config.SPOTIFY_CLIENT_ID,
            client_secret=config.SPOTIFY_CLIENT_SECRET,
        )
    )
    items = sp.search(q=query, type="track", limit=1).get("tracks", {}).get("items", [])
    if not items:
        raise RuntimeError(f"no track found for '{query}'")
    return items[0]["uri"]


def _screen_size() -> tuple:
    r = subprocess.run(
        ["osascript", "-e", 'tell application "Finder" to get bounds of window of desktop'],
        capture_output=True, text=True, timeout=10,
    )
    parts = [int(p.strip()) for p in r.stdout.split(",")]
    return parts[2], parts[3]


def _place_window(proc: str, x: int, y: int, w: int, h: int, timeout: float = 6.0,
                  screen: tuple = None) -> bool:
    """Position + size an app's front window and bring the app forward.
    Retries briefly — a freshly launched app's window may not exist yet.

    Sizes are requests, not commands: apps enforce their own minimums (Spotify
    won't go below 800x600). So after resizing we re-read the ACTUAL size and
    nudge the window back on-screen, rather than letting it hang off the edge.
    """
    clamp = ""
    if screen:
        W, H = screen
        clamp = f'''
            set actual to size of window 1
            set aw to item 1 of actual
            set ah to item 2 of actual
            set nx to {x}
            set ny to {y}
            if (nx + aw) > {W} then set nx to {W} - aw
            if nx < 0 then set nx to 0
            if (ny + ah) > {H} then set ny to {H} - ah
            if ny < 22 then set ny to 22
            set position of window 1 to {{nx, ny}}
        '''
    script = f'''
    tell application "System Events"
        if not (exists process "{proc}") then return "NOPROC"
        tell process "{proc}"
            if (count of windows) is 0 then return "NOWIN"
            set position of window 1 to {{{x}, {y}}}
            set size of window 1 to {{{w}, {h}}}
            {clamp}
            set frontmost to true
        end tell
    end tell
    return "OK"
    '''
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=10)
        if (r.stdout or "").strip() == "OK":
            return True
        time.sleep(0.4)
    return False


def _arrange_windows(cancel: threading.Event) -> None:
    """The start-day layout. Placed back-to-front — bringing each app forward
    in turn stacks them, so the LAST placed (Notes) ends up on top:
      Spotify   top-right, at the back
      ChatGPT   middle
      Claude    left
      Notes     bottom-right, frontmost
    """
    try:
        W, H = _screen_size()
    except Exception:
        return
    y0 = 30   # clear the menu bar
    notes_w, notes_h = int(W * 0.38), int(H * 0.45)
    plan = [
        ("Spotify", W - int(W * 0.30) - 8, y0, int(W * 0.30), int(H * 0.45)),
        ("Claude", 8, y0, int(W * 0.52), int(H * 0.85)),
        # Bottom-right: the on-screen clamp in _place_window keeps it flush if
        # Notes enforces a bigger minimum than we asked for.
        ("Notes", W - notes_w - 8, H - notes_h - 8, notes_w, notes_h),
    ]
    for proc, x, y, w, h in plan:
        if cancel.is_set():
            return
        _place_window(proc, x, y, w, h, screen=(W, H))


def _spotify_running() -> bool:
    return subprocess.run(["pgrep", "-x", "Spotify"], capture_output=True).returncode == 0


def _spotify_ready(timeout: float = 15.0) -> bool:
    """Wait until Spotify can actually be DRIVEN, not just launched. Answering
    `player state` happens seconds before it can accept a play command, so we
    also require a readable player position."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = subprocess.run(
            ["osascript", "-e",
             'tell application "Spotify" to (player state as string) & "|" & (player position as string)'],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0 and "|" in (r.stdout or ""):
            return True
        time.sleep(0.5)
    return False


def _ask(script: str) -> str:
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=10)
    return (r.stdout or "").strip() if r.returncode == 0 else ""


def _ensure_playing(uri: str, attempts: int = 8) -> bool:
    """Start `uri` from 0 and PROVE it's actually playing before returning.

    Answering AppleScript is not the same as being able to play: a cold Spotify
    accepts `play track` then silently stalls at position 0. So we try several
    times with growing backoff (a freshly launched app needs a few seconds to
    become drivable), verifying the playhead is genuinely advancing.
    """
    for i in range(attempts):
        # play → settle → seek to 0 → play again. The seek matters because
        # `play track` RESUMES where a previous run paused; the settle matters
        # because seeking a cold instance immediately stalls it.
        subprocess.run(
            ["osascript", "-e",
             f'tell application "Spotify"\nplay track "{uri}"\ndelay 0.6\n'
             'set player position to 0\nplay\nend tell'],
            capture_output=True, text=True, timeout=20,
        )
        time.sleep(0.6)
        if _ask('tell application "Spotify" to player state as string') == "playing":
            try:
                p1 = float(_ask('tell application "Spotify" to player position') or 0)
                time.sleep(0.4)
                p2 = float(_ask('tell application "Spotify" to player position') or 0)
                if p2 > p1:
                    return True   # the playhead is really moving
            except ValueError:
                pass
        # Not ready yet — wait longer each round (0.6, 1.2, 1.8 … up to ~3s).
        time.sleep(min(0.6 * (i + 1), 3.0))
    return False


def start_day(cancel: threading.Event | None = None, say=None) -> None:
    """Run the morning routine. `say(text)` is spoken WHILE the music plays —
    the speaking time is deducted so the song still stops at _PLAY_SECONDS."""
    cancel = cancel or threading.Event()
    opened: list = []
    failed: list = []
    lock = threading.Lock()

    def _open(app: str, fallback_url: str | None) -> None:
        try:
            if app == "Notes":
                label = _open_notes_folder()
            elif app == "Claude":
                label = _open_claude_code()
            else:
                open_app(app)
                label = app
            with lock:
                opened.append(label)
        except Exception:
            if fallback_url and subprocess.run(
                ["open", fallback_url], capture_output=True
            ).returncode == 0:
                with lock:
                    opened.append(f"{app} on the web")
            else:
                with lock:
                    failed.append(app)

    # Everything at once: the three apps AND the track search AND Spotify's
    # launch all overlap, so the music starts as fast as possible.
    openers = []
    for app, fb in _APPS:
        t = threading.Thread(target=_open, args=(app, fb), daemon=True)
        t.start()
        openers.append(t)

    found: dict = {}

    def _find() -> None:
        try:
            found["uri"] = _track_uri(_SONG)
        except Exception as exc:
            found["err"] = exc

    finder = threading.Thread(target=_find, daemon=True)
    finder.start()

    music_ok = False
    watcher = None
    try:
        cold = not _spotify_running()   # was it closed? then it needs longer
        open_app("Spotify")
        _spotify_ready(15.0)
        if cold:
            time.sleep(2.5)   # a freshly launched Spotify isn't drivable at once
        finder.join(8.0)
        if "uri" not in found:
            raise RuntimeError(str(found.get("err", "track search timed out")))
        # Play in the DESKTOP app directly — no Web-API device dance needed —
        # and don't move on until the music is provably audible.
        if not _ensure_playing(found["uri"]):
            raise RuntimeError("Spotify wouldn't start playing")
        # Arm the playhead watcher only now: it rides Spotify's own clock and
        # pauses the song at the mark regardless of what we do meanwhile.
        watcher = subprocess.Popen(
            ["osascript", "-e", _WATCH_SCRIPT],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        music_ok = True
    except Exception:
        pass

    # Speak over the music — short and to the point, no app roll-call.
    if say is not None and not cancel.is_set():
        line = "I've got here all you need to start your day, sir."
        if not music_ok:
            line += " Though Spotify wouldn't cooperate."
        try:
            say(line)
        except Exception:
            pass

    # Arrange the windows WHILE the music plays — the watcher rides Spotify's
    # own clock in its own process, so this doesn't delay the 11s stop.
    for t in openers:
        t.join(6.0)
    _arrange_windows(cancel)

    if music_ok and watcher is not None:
        # Wait for the watcher (song hits the mark) — or a barge-in, which
        # stops the music right away instead.
        while watcher.poll() is None:
            if cancel.wait(0.1):
                watcher.terminate()
                subprocess.run(
                    ["osascript", "-e", 'tell application "Spotify" to pause'],
                    capture_output=True, text=True, timeout=10,
                )
                break
    # Quit — and make sure it took. A quit arriving on the heels of the pause
    # sometimes gets dropped while Spotify is still processing, so settle
    # briefly, then verify the process is gone and retry if not.
    time.sleep(0.4)
    for _ in range(3):
        try:
            close_app("Spotify")
        except Exception:
            pass
        time.sleep(2.0)
        r = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to (name of processes) contains "Spotify"'],
            capture_output=True, text=True, timeout=10,
        )
        if (r.stdout or "").strip() == "false":
            break

    # Spotify quitting hands focus back to whatever was active before the
    # routine — put Notes back on top, where the layout wants it.
    if not cancel.is_set():
        subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to set frontmost of process "Notes" to true'],
            capture_output=True, text=True, timeout=10,
        )

    # Stay honest, but only pipe up when something actually failed.
    if failed and say is not None and not cancel.is_set():
        try:
            say("Although " + " and ".join(sorted(failed)) + " wouldn't open, sir.")
        except Exception:
            pass
