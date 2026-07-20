"""Local Mac control — open apps and drive Spotify desktop.

These are *local-only* tools (per AGENT.md's architecture note): they only
make sense on the machine the user is sitting at. For now Jarvis runs on that
machine, so they execute in-process. When the brain moves to an always-on
server (Tier 5), these same handlers relocate behind the Mac local-agent
bridge — the tool definitions here don't change.
"""

import difflib
import glob
import os
import subprocess
from typing import List, Optional

from jarvis import lifecycle
from jarvis.registry import Registry, Tool

_APP_DIRS = [
    "/Applications",
    "/Applications/Utilities",
    "/System/Applications",
    "/System/Applications/Utilities",
    os.path.expanduser("~/Applications"),
]


def _app_paths() -> dict:
    """{name: bundle path} for every installed app in the standard folders."""
    paths = {}
    for d in _APP_DIRS:
        for p in glob.glob(os.path.join(d, "*.app")):
            paths.setdefault(os.path.basename(p)[:-4], p)
    return paths


def _installed_apps() -> List[str]:
    return sorted(_app_paths())


# Words that carry no signal about WHICH app is meant.
_GENERIC = {"app", "application", "the", "my", "a", "an", "ai"}


def _resolve_app(spoken: str, candidates: Optional[List[str]] = None) -> Optional[str]:
    """The app the user MEANT: exact → substring → fuzzy ("Clode" → "Claude").
    Voice transcription mangles names — multi-word guesses like "Cloud AI" are
    also tried token-by-token — so never require an exact match."""
    if candidates is None:
        candidates = _installed_apps()
    want = spoken.strip().lower()
    if not want:
        return None
    by_lower = {c.lower(): c for c in candidates}
    if want in by_lower:
        return by_lower[want]
    subs = [c for c in candidates if want in c.lower() or c.lower() in want]
    if subs:
        return min(subs, key=len)   # tightest containment wins
    variants = {want, want.replace(" ", "")}
    variants.update(t for t in want.split() if len(t) >= 3 and t not in _GENERIC)
    best, best_score = None, 0.0
    for cand_l, cand in by_lower.items():
        for v in variants:
            score = difflib.SequenceMatcher(None, v, cand_l).ratio()
            if score > best_score:
                best, best_score = cand, score
    return best if best_score >= 0.6 else None


def _activate_running(name: str) -> bool:
    """Bring an already-running app forward WITHOUT sending it an Apple Event —
    a busy app times out `open` (-1712) but the window server still obliges."""
    r = subprocess.run(
        ["osascript", "-e",
         f'tell application "System Events" to set frontmost of process "{name}" to true'],
        capture_output=True, text=True, timeout=10,
    )
    return r.returncode == 0

# Never quit these: Finder can't meaningfully be quit, loginwindow/Dock are
# system furniture, and the user wants App Store left alone (it may be mid-
# download/update).
_NEVER_QUIT = {"Finder", "loginwindow", "Dock", "SystemUIServer", "App Store"}

# Ask System Events for one "name|pid" per line — unambiguous to parse, unlike
# `get {name, unix id} of every process`, which returns two parallel lists.
_LIST_SCRIPT = '''
tell application "System Events"
    set out to ""
    repeat with p in (every process whose background only is false)
        set out to out & (name of p) & "|" & (unix id of p) & linefeed
    end repeat
    return out
end tell
'''


def _own_pids() -> set:
    """Jarvis's own processes: this engine, the window it spawned, and the
    Jarvis.app launcher that started us. Excluded by PID because the window
    shows up under a generic name ("Python"), which is unsafe to match on."""
    pids = {os.getpid(), os.getppid()}
    try:
        out = subprocess.run(
            ["pgrep", "-P", str(os.getpid())], capture_output=True, text=True, timeout=5
        ).stdout
        pids.update(int(p) for p in out.split() if p.strip().isdigit())
    except Exception:
        pass
    return pids


def _visible_apps() -> List[tuple]:
    """[(name, pid)] for every app with a UI, minus Jarvis's own processes."""
    result = subprocess.run(
        ["osascript", "-e", _LIST_SCRIPT], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "couldn't read the list of open apps")
    mine = _own_pids()
    apps = []
    for line in result.stdout.splitlines():
        name, sep, pid = line.rpartition("|")
        if not sep or not pid.strip().isdigit():
            continue
        if int(pid) in mine:
            continue   # that's us
        apps.append((name.strip(), int(pid)))
    return apps


def list_open_apps() -> str:
    """What's actually open right now — so Jarvis never has to ask."""
    apps = [n for n, _ in _visible_apps() if n not in _NEVER_QUIT]
    if not apps:
        return "Nothing is open, sir."
    return "Currently open: " + ", ".join(apps) + "."


def close_all_apps(keep: Optional[List[str]] = None) -> str:
    """Quit every open app except Jarvis himself (and anything in `keep`).

    Reports honestly: names what actually closed and what refused.
    """
    keep_lower = {k.strip().lower() for k in (keep or [])}
    targets = [
        (n, p) for n, p in _visible_apps()
        if n not in _NEVER_QUIT and n.lower() not in keep_lower
    ]
    if not targets:
        return "Nothing to close, sir."

    closed, failed = [], []
    for name, _pid in targets:
        r = subprocess.run(
            ["osascript", "-e", f'tell application "{name}" to quit'],
            capture_output=True, text=True, timeout=15,
        )
        (closed if r.returncode == 0 else failed).append(name)

    # Don't recite what was closed — the user can see it. Failures still get
    # named, since a silent "done" would be a lie if something stayed open.
    if failed:
        return f"Done, sir — though {', '.join(failed)} wouldn't quit."
    if closed:
        return "Done, sir."
    return "Nothing to close, sir."


def shut_down() -> str:
    """Ask the engine to shut down (voice: 'shut down', 'goodbye')."""
    lifecycle.request_shutdown()
    return "Shutting down."


def open_app(name: str) -> str:
    # Resolve what was SAID to what's actually installed ("Clode"/"Cloud AI"
    # still means Claude), then open by bundle path — the most reliable route.
    resolved = _resolve_app(name)
    target = resolved or name.strip()
    path = _app_paths().get(target)
    cmd = ["open", path] if path else ["open", "-a", target]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        result = None
    if result is not None and result.returncode == 0:
        return f"Opened {target}."
    # `open` times out (-1712) when the app is running but busy. It IS open —
    # just bring it forward instead of falsely claiming it doesn't exist.
    if _activate_running(target):
        return f"{target} was already open, sir — brought it forward."
    raise RuntimeError(f"couldn't find anything like '{name}' to open")


def close_app(name: str) -> str:
    """Quit a running Mac app by name — but only if it's actually running, and
    report honestly (don't claim success when nothing closed)."""
    # List currently-running (non-background) apps.
    listing = subprocess.run(
        ["osascript", "-e",
         'tell application "System Events" to get name of every process whose background only is false'],
        capture_output=True, text=True,
    )
    running = [a.strip() for a in listing.stdout.split(",") if a.strip()]
    match = _resolve_app(name, candidates=running)
    if not match:
        # Maybe they meant an installed app that simply isn't running.
        installed = _resolve_app(name)
        if installed:
            return f"{installed} isn't open."
        return f"{name} isn't open."
    subprocess.run(["osascript", "-e", f'tell application "{match}" to quit'],
                   capture_output=True, text=True)
    return f"Done — closed {match}."


def _osascript(script: str) -> str:
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "AppleScript failed")
    return result.stdout.strip()


def spotify(action: str, uri: Optional[str] = None) -> str:
    action = action.lower()
    if action == "play":
        _osascript('tell application "Spotify" to play')
        return "Playing."
    if action == "pause":
        _osascript('tell application "Spotify" to pause')
        return "Paused."
    if action == "next":
        _osascript('tell application "Spotify" to next track')
        return "Skipped to next track."
    if action == "previous":
        _osascript('tell application "Spotify" to previous track')
        return "Went to previous track."
    if action == "current":
        return _osascript(
            'tell application "Spotify" to (name of current track) '
            '& " — " & (artist of current track)'
        )
    if action == "play_uri":
        if not uri:
            raise ValueError("play_uri requires a Spotify URI (e.g. spotify:track:...).")
        _osascript(f'tell application "Spotify" to play track "{uri}"')
        return f"Playing {uri}."
    raise ValueError(f"Unknown Spotify action '{action}'.")


def register(registry: Registry) -> None:
    registry.register(
        Tool(
            name="open_app",
            description="Open a macOS application by name (e.g. 'Spotify', 'Safari', 'Notes'). Use when the user asks to open or launch an app.",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The app's name as it appears in /Applications."}
                },
                "required": ["name"],
            },
            handler=open_app,
        )
    )
    registry.register(
        Tool(
            name="close_app",
            description="Quit/close a running macOS application by name. Use when the user asks to close, quit, or stop an app.",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The app's name, e.g. 'Spotify', 'Safari'."}
                },
                "required": ["name"],
            },
            handler=close_app,
        )
    )
    registry.register(
        Tool(
            name="list_open_apps",
            description=(
                "See which applications are currently open on the Mac. Call this "
                "whenever the user refers to their open apps without naming them "
                "(e.g. 'what do I have open?', 'close everything'). Never ask the "
                "user which apps are open — check with this instead."
            ),
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=list_open_apps,
        )
    )
    registry.register(
        Tool(
            name="close_all_apps",
            description=(
                "Quit every open application at once, except Jarvis himself. Use "
                "when the user says to close all apps / close everything / shut "
                "everything down. Do NOT call list_open_apps first — this already "
                "works out what's open. Optionally pass `keep` to spare some apps "
                "(e.g. 'close everything except Spotify'). Report the result "
                "briefly — the user does NOT want the closed apps listed back."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "keep": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "App names to leave running, e.g. ['Spotify'].",
                    }
                },
                "required": [],
            },
            handler=close_all_apps,
        )
    )
    registry.register(
        Tool(
            name="shut_down",
            description="Shut Jarvis down completely. Use when the user says goodbye, shut down, stop listening, or that they're done. Say a brief goodbye first.",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=shut_down,
        )
    )
    registry.register(
        Tool(
            name="spotify",
            description=(
                "Control Spotify desktop playback. action is one of: 'play', "
                "'pause', 'next', 'previous', 'current' (what's playing now), or "
                "'play_uri' (play a specific Spotify track/album/playlist URI). "
                "Playing a song by name isn't supported yet — that needs the "
                "Spotify Web API, which is a later addition."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["play", "pause", "next", "previous", "current", "play_uri"],
                        "description": "The playback action.",
                    },
                    "uri": {
                        "type": "string",
                        "description": "A Spotify URI, required only for the 'play_uri' action.",
                    },
                },
                "required": ["action"],
            },
            handler=spotify,
        )
    )
