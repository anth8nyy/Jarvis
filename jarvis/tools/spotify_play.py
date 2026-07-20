"""Play a song by name via the Spotify Web API.

Needs a one-time login (SPOTIFY_CLIENT_ID/SECRET in .env + a browser auth the
first run, cached to data/.spotify_cache). Requires Spotify Premium to start
playback. The desktop Spotify app should be open so there's an active device.
"""

from __future__ import annotations

import os

from jarvis.registry import Registry, Tool

_CACHE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", ".spotify_cache"
)

_client = None


def _spotify():
    global _client
    if _client is not None:
        return _client
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth

    from jarvis import config

    if not config.SPOTIFY_CLIENT_ID or not config.SPOTIFY_CLIENT_SECRET:
        raise RuntimeError(
            "Spotify isn't set up yet — add SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET to .env."
        )
    os.makedirs(os.path.dirname(_CACHE), exist_ok=True)
    auth = SpotifyOAuth(
        client_id=config.SPOTIFY_CLIENT_ID,
        client_secret=config.SPOTIFY_CLIENT_SECRET,
        redirect_uri=config.SPOTIFY_REDIRECT_URI,
        scope="user-modify-playback-state user-read-playback-state",
        cache_path=_CACHE,
        open_browser=True,
    )
    _client = spotipy.Spotify(auth_manager=auth)
    return _client


def _ensure_device(sp) -> str:
    devices = sp.devices().get("devices", [])
    if not devices:
        raise RuntimeError("No active Spotify device — open the Spotify app first.")
    active = next((d for d in devices if d.get("is_active")), devices[0])
    return active["id"]


def _play_uris_in_app(uris: list) -> bool:
    """Play track URIs through the DESKTOP Spotify app via AppleScript.

    Far more reliable than the Web API's start_playback, which fails with "no
    active device" whenever Spotify is closed or idle — exactly when the user
    asks. This opens Spotify if needed, waits for it to become drivable, then
    plays, verifying the playhead actually advances (a cold app accepts the
    command then stalls). Queueing multiple URIs isn't scriptable, so we play
    the first and rely on the user's context/radio for the rest.
    """
    import subprocess
    import time

    def sh(cmd):
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20)

    def osa(script):
        r = sh(["osascript", "-e", script])
        return (r.stdout or "").strip() if r.returncode == 0 else ""

    if not uris:
        return False
    cold = subprocess.run(["pgrep", "-x", "Spotify"], capture_output=True).returncode != 0
    if cold:
        subprocess.run(["open", "-a", "Spotify"], capture_output=True)
    # Wait until it can be driven (answers with a readable player position).
    t0 = time.time()
    while time.time() - t0 < 15:
        if "|" in osa('tell application "Spotify" to (player state as string) & "|" & (player position as string)'):
            break
        time.sleep(0.5)
    if cold:
        time.sleep(2.0)   # a freshly launched Spotify isn't playable instantly
    uri = uris[0]
    for i in range(8):
        sh(["osascript", "-e", f'tell application "Spotify" to play track "{uri}"'])
        time.sleep(0.6)
        if osa('tell application "Spotify" to player state as string') == "playing":
            try:
                p1 = float(osa('tell application "Spotify" to player position') or 0)
                time.sleep(0.4)
                p2 = float(osa('tell application "Spotify" to player position') or 0)
                if p2 >= p1:
                    return True
            except ValueError:
                pass
        time.sleep(min(0.5 * (i + 1), 2.5))
    return False


def play_song(query: str) -> str:
    """Search for a track and start playing it."""
    sp = _spotify()
    # limit=5, not 1: a limit of 1 returns an unreliable result from these
    # credentials. The wider search's first hit is correctly relevance-ranked.
    results = sp.search(q=query, type="track", limit=5)
    items = results.get("tracks", {}).get("items", [])
    if not items:
        return f"Couldn't find a song matching '{query}' on Spotify."
    track = items[0]
    artists = ", ".join(a["name"] for a in track["artists"])
    if not _play_uris_in_app([track["uri"]]):
        return f"I found “{track['name']}” but Spotify wouldn't play it, sir."
    return f"Playing “{track['name']}” by {artists}."


# This app's Spotify credentials are in development mode, which caps search
# `limit` at 10 (a higher value 400s with a misleading "Invalid limit").
_SEARCH_LIMIT = 10


def _find_artist(sp, wanted: str):
    """The artist the user meant, or None.

    Two Spotify behaviours make the naive `search(limit=1)[0]` wrong:
    * limit=1 returns an unreliable result ("Coldplay" came back as Bruno Mars),
      while limit=5 ranks Coldplay first — so search wide and choose ourselves.
    * search NEVER returns empty; nonsense gives unrelated artists. Without a
      similarity check, "play some <gibberish>" cheerfully played strangers.
    """
    import difflib

    items = sp.search(q=wanted, type="artist", limit=5).get("artists", {}).get("items", [])
    if not items:
        return None
    w = wanted.strip().lower()
    best, score = None, 0.0
    for a in items:
        s = difflib.SequenceMatcher(None, w, a["name"].strip().lower()).ratio()
        if s > score:
            best, score = a, s
    return best if score >= 0.55 else None


def play_artist(artist: str) -> str:
    """Play a shuffled selection of an artist's tracks.

    Uses a track SEARCH rather than the artist-top-tracks endpoint: Spotify
    returns 403 Forbidden on top-tracks for these credentials, which silently
    broke "play some <artist>" entirely.
    """
    import random

    sp = _spotify()
    art = _find_artist(sp, artist)
    if art is None:
        return f"Couldn't find an artist called '{artist}' on Spotify, sir."
    name = art["name"]

    # Exact-artist filter first; fall back to a plain search, keeping only the
    # tracks actually credited to them.
    tracks = sp.search(q=f'artist:"{name}"', type="track", limit=_SEARCH_LIMIT)
    uris = [t["uri"] for t in tracks.get("tracks", {}).get("items", [])]
    if not uris:
        loose = sp.search(q=name, type="track", limit=_SEARCH_LIMIT)
        uris = [
            t["uri"] for t in loose.get("tracks", {}).get("items", [])
            if any(a["name"].lower() == name.lower() for a in t["artists"])
        ]
    if not uris:
        return f"No tracks found for {name}, sir."
    random.shuffle(uris)
    if not _play_uris_in_app(uris):
        return f"I found {name} but Spotify wouldn't play, sir."
    return f"Playing some {name}, sir."


def register(registry: Registry) -> None:
    registry.register(
        Tool(
            name="play_song",
            description=(
                "Search Spotify for a song by name (and optionally artist) and play "
                "it on the user's active Spotify device. Use when they say 'play "
                "<song>' or 'play <song> by <artist>'. Requires Spotify open and Premium."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Song name, optionally with artist, e.g. 'Blinding Lights The Weeknd'.",
                    }
                },
                "required": ["query"],
            },
            handler=play_song,
        )
    )
    registry.register(
        Tool(
            name="play_artist",
            description="Play a shuffled mix of a specific artist's popular songs. Use for 'play some <artist>', 'play random <artist> songs', 'put on some <artist>'.",
            input_schema={
                "type": "object",
                "properties": {"artist": {"type": "string", "description": "The artist's name."}},
                "required": ["artist"],
            },
            handler=play_artist,
        )
    )
