"""Jarvis — an always-on voice assistant with a native reactor-circle window.

The engine (this process) owns the mic: wake word → capture → Deepgram STT →
Brain (+ tools) → speak with a British voice via macOS `say`. A tiny local
HTTP server exposes live state (listening / thinking / speaking / muted); a
SEPARATE process renders the circle window and polls it. Keeping the window in
its own process is what avoids the CoreAudio -50 conflict with the mic.

Everything reuses the shared core (brain, tools, memory, heartbeat, rails).
"""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from jarvis import lifecycle, transcript
from jarvis.brain import Brain
from jarvis.heartbeat import Heartbeat
from jarvis.tools.mac import close_all_apps, close_app, open_app
from jarvis.tools.spotify_play import play_artist, play_song
from jarvis.tools.tasks import remind_me as tasks_remind
from jarvis.voice import audio, stt
from jarvis.voice.speech import Speaker
from jarvis.wake import WakeListener

_UI = os.path.join(os.path.dirname(__file__), "ui.html")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
# Strips the wake word off the front of a command. The name list covers what
# Deepgram actually writes when it mishears it ("Jeremy" is from a real log);
# ['’]?s? absorbs nova-3's habit of rendering the name possessive ("Jarvis's"), which
# otherwise leaves a stray apostrophe as the first "word".
_WAKE_PREFIX = re.compile(
    r"^\s*(hey\s+)?(jarvis|jarvi|jervis|jeremy|travis|charest)['’]?s?[\s,.!]*",
    re.IGNORECASE,
)

# Fast-path patterns: handled locally, skipping the AI model entirely so common
# commands (open/close an app, play a song, goodbye) respond near-instantly.

# Yes/no for the confirmation gate. Negatives are checked FIRST and both use
# word boundaries: a substring test made "no, please don't" read as YES (it
# contains "please"), which would have sent a message the user just refused.
_NO_RE = re.compile(
    r"\b(?:no|nope|nah|don'?t|do not|stop|cancel|forget it|never ?mind|negative|wait)\b",
    re.IGNORECASE,
)
_YES_RE = re.compile(
    r"\b(?:yes|yeah|yep|yup|sure|ok|okay|go ahead|do it|send it|affirmative|confirm|correct|please do)\b",
    re.IGNORECASE,
)


def _is_yes(answer: str) -> bool:
    """Only an explicit yes counts — silence, noise or anything unclear is NO."""
    if not answer or not answer.strip():
        return False
    if _NO_RE.search(answer):
        return False
    return bool(_YES_RE.search(answer))


_OPEN_RE = re.compile(r"^(?:open\w*|launch\w*|start\w*)\s+(.+)$", re.IGNORECASE)
_CLOSE_RE = re.compile(r"^(?:close\w*|quit\w*|exit\w*)\s+(.+)$", re.IGNORECASE)
_ARTIST_RE = re.compile(r"^(?:play|put on)\s+(?:some|random)\s+(.+?)(?:\s+songs?)?$", re.IGNORECASE)
_PLAY_RE = re.compile(r"^(?:play\w*)\s+(.+)$", re.IGNORECASE)
_TIMER_RE = re.compile(r"^(?:set (?:a )?timer for|start (?:a )?timer for)\s+(.+)$", re.IGNORECASE)
_REMIND_RE = re.compile(r"^remind me(?:\s+to)?\s+(.+?)\s+in\s+(.+)$", re.IGNORECASE)
_DUR_RE = re.compile(r"(\d+)\s*(second|sec|minute|min|hour|hr)s?", re.IGNORECASE)
_BYE_WORDS = ("goodbye", "good bye", "bye bye", "shut down", "shutdown", "stop listening",
              "power down", "bye jarvis", "close jarvis", "turn off", "see you")
# Politeness / filler words to strip off the end of a command so they don't
# break the fast-path ("open spotify please" → "open spotify").
_FILLER_RE = re.compile(
    r"[\s,]*\b(please|now|for me|thanks|thank you|buddy|pal|mate|man|dude|okay|ok|jarvis|right now)\b[\s.,!?]*$",
    re.IGNORECASE,
)


def _strip_filler(s: str) -> str:
    prev = None
    while s and s != prev:
        prev = s
        s = _FILLER_RE.sub("", s).strip()
    return s


def _parse_duration(text: str) -> int:
    """Parse '5 minutes', '90 seconds', '1 hour 30 minutes' → total seconds. 0 if none."""
    total = 0
    for num, unit in _DUR_RE.findall(text):
        n, u = int(num), unit.lower()
        total += n * (1 if u.startswith("s") else 60 if u.startswith("m") else 3600)
    # "a minute" / "an hour"
    if not total:
        if re.search(r"\ba (?:minute|min)\b", text, re.I):
            total = 60
        elif re.search(r"\ban? hour\b", text, re.I):
            total = 3600
    return total

# Live state the circle window polls. Atomic dict writes are all it needs.
STATE = {"state": "listening", "muted": False, "recording": False}
LAST_POLL = [0.0]

_engine = None  # set in run(), so the HTTP handler can reach the engine


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence request logging
        pass

    def _json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path.startswith("/togglemute"):
            if _engine:
                _engine.toggle_mute()
            self._json(STATE)
        elif self.path.startswith("/unmute"):
            if _engine:
                _engine.set_muted(False)
            self._json(STATE)
        elif self.path.startswith("/mute"):
            if _engine:
                _engine.set_muted(True)
            self._json(STATE)
        elif self.path.startswith("/startsave"):
            if _engine:
                _engine.start_recording()
            self._json(STATE)
        elif self.path.startswith("/show"):
            if _engine:
                _engine._ensure_window()
            self._json(STATE)
        elif self.path.startswith("/export"):
            path = _engine.export_conversation() if _engine else None
            self._json({"ok": bool(path), "path": path or ""})
        elif self.path.startswith("/quitall"):
            # Used by the hotkey daemon (⌃⌥Q). Reply first — _quit_completely
            # never returns.
            self._json({"ok": True})
            if _engine:
                threading.Thread(target=_engine._quit_completely, daemon=True).start()
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path.startswith("/state"):
            LAST_POLL[0] = time.time()
            self._json(STATE)
        elif self.path.startswith("/sphere.png"):
            home = os.path.expanduser("~")
            candidates = [
                os.path.join(os.path.dirname(_UI), "sphere.png"),
                os.path.join(home, "Desktop", "sphere.png"),
                os.path.join(home, "Downloads", "sphere.png"),
                os.path.join(home, "Desktop", "jarvis.png"),
                os.path.join(home, "Downloads", "jarvis.png"),
            ]
            path = next((p for p in candidates if os.path.exists(p)), None)
            if path:
                with open(path, "rb") as fh:
                    body = fh.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()
        else:
            with open(_UI, "rb") as fh:
                body = fh.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)


class JarvisEngine:
    def __init__(self) -> None:
        self.speaker = Speaker()
        self.brain = Brain(confirmer=self._voice_confirm)
        self.wake = WakeListener(
            on_wake=self._on_wake,
            on_interrupt=self._interrupt,
            # Position-aware wake: mid-sentence "Jarvis" only counts while he's
            # actually speaking (= barge-in), never while you're dictating.
            is_speaking=lambda: STATE["state"] == "speaking",
            # Spoken routines that fire even with "Jarvis" at the END of the
            # sentence ("let's start the day, Jarvis"). Fuzzy on purpose —
            # vosk hears "start of the day" / "started the day" etc.
            routine_phrases={
                re.compile(r"\bstart\w*\s+(?:of\s+)?(?:the\s+|my\s+|our\s+|a\s+)?day\b"):
                    self._start_day_routine,
            },
        )
        self.heartbeat = Heartbeat(on_interrupt=self._on_reminder)
        self._busy = threading.Lock()
        self._cancel = threading.Event()   # set when a new "Jarvis" barges in
        self._window_proc = None
        # Exact-time timers (threading.Timer) announce through here.
        lifecycle.register_reminder_sink(self._announce)

    # --- circle state + speaking -----------------------------------------
    def _set(self, s: str) -> None:
        STATE["state"] = s

    @staticmethod
    def _says_own_name(text: str) -> bool:
        """Would speaking this text make him hear his OWN wake word? Reading a
        message like "Jarvis εισαι καλος" aloud used to wake him mid-sentence,
        so he answered himself with "At your service, sir"."""
        return "jarvis" in (text or "").lower().replace(" ", "")

    def _speak_safely(self, text: str, voice=None) -> None:
        """Speak, deafening the wake listener if the words contain his name so
        he can't self-trigger. Normal speech keeps the mic open for barge-in."""
        guard = self._says_own_name(text)
        if guard:
            self.wake.pause()
        try:
            self.speaker.speak(text, voice=voice) if voice else self.speaker.speak(text)
        finally:
            if guard and not STATE["muted"]:
                self.wake.resume()

    def _say(self, text: str) -> None:
        """Speak — closing the mic ONLY while actually talking, so Jarvis stays
        deaf for the shortest possible time (not through his whole 'thinking')."""
        if self._cancel.is_set():
            return
        if STATE["recording"]:
            transcript.log("Jarvis", text)
        self._set("speaking")
        try:
            self._speak_safely(text)   # mic stays open (unless he says his own name)
        finally:
            self._set("muted" if STATE["muted"] else "listening")

    def _interrupt(self) -> None:
        """Barge-in: fired the instant the wake word is heard — cut off any
        current speech and cancel the in-flight reply."""
        self._cancel.set()
        self.speaker.stop()

    def start_recording(self) -> None:
        """Begin a FRESH transcript from now on (wipes any previous one)."""
        transcript.clear()
        STATE["recording"] = True

    def export_conversation(self) -> str | None:
        """Write the recorded transcript to a Word doc on the Desktop and open
        it. Stops recording afterward."""
        try:
            path = transcript.export_docx()
            subprocess.run(["open", path], capture_output=True)
            STATE["recording"] = False
            return path
        except Exception as exc:
            print(f"[export failed: {exc}]", flush=True)
            return None

    def set_muted(self, muted: bool) -> None:
        if muted == STATE["muted"]:
            return
        STATE["muted"] = muted
        if muted:
            self.wake.pause()
            self.speaker.stop()
            self._set("muted")
        else:
            self.wake.resume()
            self._set("listening")

    def toggle_mute(self) -> None:
        self.set_muted(not STATE["muted"])

    # --- a full spoken turn ----------------------------------------------
    _DUCK_TO = 20        # music volume (%) while a message is read out
    _duck_prev = None    # the exact level to put back afterwards

    def _hold_music(self, on: bool) -> None:
        """Drop Spotify to 20% while a message is read aloud, then restore it to
        EXACTLY the level it was at. Used only for unprompted announcements —
        normal conversation never touches the music. Spotify has its own volume,
        so his voice stays at full loudness."""
        import subprocess

        def osa(script, timeout=4):
            r = subprocess.run(["osascript", "-e", script],
                               capture_output=True, text=True, timeout=timeout)
            return (r.stdout or "").strip() if r.returncode == 0 else ""

        try:
            if subprocess.run(["pgrep", "-x", "Spotify"], capture_output=True).returncode != 0:
                return                      # not running — nothing to duck
            if on:
                if self._duck_prev is not None:
                    return                  # already ducked
                if osa('tell application "Spotify" to player state as string') != "playing":
                    return                  # nothing playing — leave it alone
                cur = osa('tell application "Spotify" to sound volume')
                prev = int(cur) if cur.isdigit() else None
                if prev is not None and prev > self._DUCK_TO:
                    osa(f'tell application "Spotify" to set sound volume to {self._DUCK_TO}')
                    self._duck_prev = prev
            elif self._duck_prev is not None:
                osa(f'tell application "Spotify" to set sound volume to {self._duck_prev}')
                self._duck_prev = None
        except Exception:
            self._duck_prev = None

    def _on_wake(self, wav: bytes) -> None:
        # Barge-in already stopped the old speech via _interrupt; wait (briefly)
        # for the previous turn to release, then take over.
        if not self._busy.acquire(timeout=8):
            return
        try:
            self._cancel.clear()
            self.speaker.stop()
            self._ensure_window()   # pop the window up if it's closed (Siri-style)
            self._set("thinking")
            try:
                heard = stt.transcribe(wav)
            except stt.STTError as exc:
                print(f"[stt error: {exc}]", flush=True)
                self._say("Ugh, didn't catch that.")
                return
            command = _strip_filler(_WAKE_PREFIX.sub("", heard).strip())
            spike = self.wake.spike_wake
            self.wake.spike_wake = False
            if not command:
                if spike:
                    # Loudness barge-in that transcribed to nothing (noise or a
                    # cough): he already went quiet — fail SILENTLY, no nagging.
                    self._set("listening")
                    return
                command = self._listen_command()   # only the wake word — grab the command
            if not command:
                self._set("listening")
                return
            self._do_turn(command)
            # Follow-up: keep listening for a few seconds so you can keep talking
            # WITHOUT saying "Jarvis" again.
            if not self._cancel.is_set():
                self._followup_loop()
        finally:
            if not STATE["muted"]:
                self._set("listening")
            self._busy.release()
            if lifecycle.shutdown_requested():
                self._quit_completely()

    def _do_turn(self, command: str) -> None:
        print(f"you> {command}", flush=True)
        if STATE["recording"]:
            transcript.log("You", command)
        if not self._fast_path(command):
            self._converse(command)

    def _acknowledge(self) -> None:
        """A short spoken reply the instant he wakes — so you KNOW he heard you
        and is listening, instead of silence that feels like he ignored you."""
        self.speaker.speak("At your service, sir.")

    def _listen_command(self) -> str:
        """Record + transcribe a command right now (used when only 'Jarvis' was said)."""
        self.wake.pause()
        try:
            self._acknowledge()   # "Yes, sir?" — audible proof he's listening
            wav = audio.record_until_silence()
        finally:
            self.wake.resume()
        if not wav:
            return ""
        try:
            return _strip_filler(_WAKE_PREFIX.sub("", stt.transcribe(wav).strip()).strip())
        except stt.STTError:
            return ""

    def _followup_loop(self) -> None:
        """After a reply, keep the conversation going hands-free: listen a few
        seconds; if you speak, handle it; if you go quiet, drop back to needing
        the wake word. The wake listener stays live during each reply, so you
        can say 'Jarvis' to cut him off at any time."""
        try:
            while not STATE["muted"] and not lifecycle.shutdown_requested() and not self._cancel.is_set():
                self._set("listening")
                self.wake.pause()   # take the mic to record your next command
                # Generous window (30s) and a sensitive start threshold, so the
                # conversation keeps flowing without repeating "Jarvis".
                wav = audio.record_until_silence(start_timeout=30.0, silence_ms=800, threshold=170)
                if not STATE["muted"]:
                    self.wake.resume()   # give the mic back so "Jarvis" can interrupt the reply
                if not wav:
                    break  # you went quiet — back to wake-word mode
                try:
                    cmd = _strip_filler(_WAKE_PREFIX.sub("", stt.transcribe(wav).strip()).strip())
                except stt.STTError:
                    cmd = ""
                if not cmd:
                    break
                self._cancel.clear()
                self._do_turn(cmd)
        finally:
            if not STATE["muted"]:
                self.wake.resume()

    # --- instant local commands (no AI round-trip) -----------------------
    def _instant(self, low: str) -> bool:
        """Everyday status/control commands answered with ZERO AI — matched by
        keyword and run directly, so they're free and sub-second. The local
        brain is only for things that actually need thinking."""
        from jarvis.tools import system as sysm
        from jarvis.tools import calendar as cal
        from jarvis.tools import weather as wx

        def has(*ws):
            return any(w in low for w in ws)

        # Any explicit web/search request goes to the brain — never a shortcut.
        # ("look on Google what time the final starts" is a SEARCH, not a clock
        # query.)
        if re.search(r"\b(google|search|look up|look it up|web|wikipedia|"
                     r"who is|who was|what is a|whats a)\b", low):
            return False

        # volume: explicit level, or up/down/max/mute
        m = re.search(r"\b(?:volume|sound)\b.*?(\d{1,3})|\bset (?:the )?volume to (\d{1,3})", low)
        if m and ("volume" in low or "sound" in low):
            self._say(sysm.set_volume(int(m.group(1) or m.group(2)))); return True
        if has("volume", "sound", "louder", "quieter", "turn it up", "turn it down"):
            if has("up", "louder", "higher", "raise"):
                self._say(sysm.set_volume(min(100, self._cur_vol() + 20))); return True
            if has("down", "lower", "quieter", "softer"):
                self._say(sysm.set_volume(max(0, self._cur_vol() - 20))); return True
            if has("max", "full", "hundred"):
                self._say(sysm.set_volume(100)); return True
            if has("what", "how", "current"):
                self._say(sysm.get_volume()); return True

        # GUARD: everything below is READ-ONLY status. If the command is really
        # an action ("create a reminder on my calendar", "remind me", "add",
        # "delete") the instant path must NOT hijack it as a read — hand it to
        # the brain, which actually does it. This was making Jarvis say he'd
        # done things he only read.
        if re.search(r"\b(creat|add|schedul|remind|make|new|put|move|reschedul|"
                     r"cancel|delet|remove|book|send|text|message|email|call|"
                     r"set up|write|note down)\w*\b", low):
            return False

        if has("battery", "charge", "charging"):
            self._say(sysm.battery()); return True
        if has("disk", "storage", "space left", "how much space"):
            self._say(sysm.disk()); return True
        if ("wifi" in low or "wi-fi" in low or "network" in low or "my ip" in low):
            self._say(sysm.wifi()); return True
        if "uptime" in low or ("how long" in low and ("on" in low or "running" in low or "up" in low)):
            self._say(sysm.uptime()); return True
        if has("resolution", "how many monitors", "how many screens", "my display"):
            self._say(sysm.screens()); return True

        if has("weather", "temperature", "how hot", "how cold", "raining", "forecast"):
            self._set("thinking")
            try:
                self._say(wx.get_weather())
            except Exception:
                self._say("I couldn't reach the weather just now, sir.")
            return True

        # ONLY the current clock — "what time is it", "what's the time", "time
        # now". NOT "what time does the match start" (that's an event → brain).
        if re.search(r"\bwhat time is it\b|\bwhat('?s| is) the time\b|"
                     r"\b(the )?time (right )?now\b|\bcurrent time\b|\btell me the time\b", low):
            from jarvis.tools.datetime_tool import get_datetime
            self._say(get_datetime()); return True
        if re.search(r"\bwhat('?s| is) (the |today'?s )?date\b|\bwhat day is (it|today)\b|"
                     r"\btoday'?s date\b", low):
            from jarvis.tools.datetime_tool import get_datetime
            self._say(get_datetime()); return True

        if ("calendar" in low or "schedule" in low or "planned" in low or "agenda" in low) \
                and has("today", "day", "what", "my"):
            self._set("thinking")
            try:
                self._say(cal.list_today_events())
            except Exception:
                self._say("I couldn't read your calendar, sir.")
            return True

        return False

    def _cur_vol(self) -> int:
        from jarvis.tools import system as sysm
        import re as _re
        m = _re.search(r"(\d+)", sysm.get_volume())
        return int(m.group(1)) if m else 50

    def _fast_path(self, command: str) -> bool:
        c = command.strip().rstrip(".!?")
        low = c.lower()

        # Instant, AI-free common commands first — the sub-second path.
        if self._instant(low):
            return True

        if ("conversation" in low or "our chat" in low) and any(
            v in low for v in ("save", "export", "download", "word", "document")
        ):
            if self.export_conversation():
                self._say("Done — saved our whole chat to your Desktop as a Word doc.")
            else:
                self._say("Hmm, couldn't save that one.")
            return True

        if any(w in low for w in _BYE_WORDS):
            self._say("Very good, sir. Powering down.")
            self._quit_completely()
            return True

        if ("unmute" not in low) and (
            re.search(r"\bmute\b", low) or "be quiet" in low or "silence" in low
        ):
            if not STATE["muted"]:
                self._say("Muting, sir.")
                self.toggle_mute()
            return True

        # "start the day" — the morning routine. Must beat
        # _OPEN_RE, whose `start\w*` prefix would otherwise try to open an app
        # called "the day".
        if re.search(
            r"\bstart\w*\s+(?:of\s+)?(?:the\s+|my\s+|our\s+|a\s+)?day\b", low
        ):
            self._start_day_core()   # turn lock is already held by this turn
            return True

        m = _OPEN_RE.match(c)
        if m:
            app = self._clean_app(m.group(1))
            try:
                open_app(app)
                self._say(f"Right away, sir. Opening {app}.")
            except Exception:
                self._say(f"I couldn't find {app}, sir.")
            return True

        # "close all the apps" / "close everything" → close-all (never Jarvis,
        # Finder or App Store). Must beat _CLOSE_RE, which would fuzzy-match
        # "everything" against some poor app. Requests with an exception
        # ("except Spotify") fall through to the brain, which passes `keep`.
        if (
            re.search(r"\b(?:close|quit|shut)\b", low)
            and re.search(r"\b(?:everything|all)\b", low)
            and not re.search(r"\b(?:except|but|besides|apart)\b", low)
        ):
            self._set("thinking")
            self._say(close_all_apps())
            return True

        m = _CLOSE_RE.match(c)
        if m:
            app = self._clean_app(m.group(1))
            self._say(close_app(app))   # honest: says if it wasn't even open
            return True

        # "play my <name> playlist" / "play the <name> playlist" — a named
        # library playlist, not a song. Must beat _PLAY_RE.
        mpl = re.match(r"^(?:play|put on)\s+(?:my|the)?\s*(.+?)\s+playlist\b", c, re.IGNORECASE)
        if mpl or ("playlist" in low and re.match(r"^(?:play|put on)\b", low)):
            from jarvis.tools.spotify_play import play_playlist
            name = mpl.group(1) if mpl else re.sub(
                r"^(?:play|put on)\s+(?:my|the)?\s*|\s*playlist\b", "", c, flags=re.IGNORECASE).strip()
            self._set("thinking")
            try:
                self._say(play_playlist(name))
            except Exception:
                self._say("I couldn't play that playlist, sir.")
            return True

        m = _ARTIST_RE.match(c)   # "play some <artist>" — check before plain play
        if m:
            self._set("thinking")
            try:
                self._say(play_artist(m.group(1)))
            except Exception:
                self._say("I couldn't play that, sir. Is Spotify open?")
            return True

        m = _PLAY_RE.match(c)
        if m:
            self._set("thinking")
            try:
                self._say(play_song(m.group(1)) or "Playing, sir.")
            except Exception:
                self._say("I couldn't play that, sir. Is Spotify open?")
            return True

        m = _TIMER_RE.match(c)
        if m:
            secs = _parse_duration(m.group(1))
            if secs:
                self._say(tasks_remind("time's up", secs))
                return True

        m = _REMIND_RE.match(c)
        if m:
            secs = _parse_duration(m.group(2))
            if secs:
                self._say(tasks_remind(m.group(1).strip(), secs))
                return True

        return False

    @staticmethod
    def _clean_app(name: str) -> str:
        name = name.strip().strip(".").strip()
        if name.lower().startswith("the "):
            name = name[4:]
        for suf in (" app", " application"):
            if name.lower().endswith(suf):
                name = name[: -len(suf)]
        return name.strip()

    def _converse(self, text: str) -> None:
        # Get the whole reply first (mic stays OPEN while the AI thinks), then
        # speak it in one go. Only what he says AFTER a tool ran is kept, so a
        # pre-tool "I'll send that, sir" preamble can't contradict the result.
        reply = ""
        spoke_itself = False
        for event in self.brain.turn(text):
            if self._cancel.is_set():
                return   # user barged in — drop this reply
            if event["type"] == "text":
                reply += event["text"]
            elif event["type"] == "tool":
                reply = ""   # drop pre-tool preamble
                if event["name"] == "start_day":
                    spoke_itself = True
            elif event["type"] == "error":
                reply += f" Oops — {event['text']}."
        reply = reply.strip()
        # "IGNORED" = the brain judged this was the TV / other people, not a
        # command addressed to him. Stay silent — never say the word aloud.
        if "IGNORED" in reply.upper() and len(reply) < 20:
            return
        if reply and not spoke_itself and not self._cancel.is_set():
            self._say(reply)

    # --- spoken confirmation gate ----------------------------------------
    @staticmethod
    def _confirm_question(request: dict) -> str:
        """Read back what he's ACTUALLY about to do, so "are you sure?" is
        answerable — a generic 'sure you want me to do that?' isn't."""
        name = request.get("name") or ""
        args = request.get("input") or {}
        if name == "send_message":
            to = args.get("recipient", "them")
            body = args.get("text", "")
            return f"Are you sure you want me to send {to}: {body}?"
        if name == "create_calendar_event":
            title = args.get("title", "that")
            when = args.get("start", "")
            try:
                from datetime import datetime
                when = datetime.fromisoformat(when).strftime("%A at %-I:%M %p")
            except Exception:
                pass
            return f"Are you sure you want me to put {title} on your calendar for {when}?"
        desc = (request.get("description") or "that").split(".")[0]
        return f"Are you sure you want me to {desc}?"

    def _ask_once(self, question: str) -> str:
        """Speak a question and return what was said back (lowercased)."""
        # Hold the wake listener off while we ask and record the yes/no, so its
        # mic stream doesn't collide with the confirmation recording — and so
        # a name inside the text being read back can't trigger a barge-in.
        self.wake.pause()
        try:
            self._set("speaking")
            self.speaker.speak(question)
            self._set("thinking")
            # Generous: a surprised "erm… yes" must not be read as refusal.
            wav = audio.record_until_silence(max_seconds=10, start_timeout=8, silence_ms=700)
        finally:
            if not STATE["muted"]:
                self.wake.resume()
        if not wav:
            return ""
        try:
            return stt.transcribe(wav).strip().lower()
        except stt.STTError:
            return ""

    def _voice_confirm(self, request: dict) -> bool:
        answer = self._ask_once(self._confirm_question(request))
        print(f"[confirm] {request.get('name')} heard {answer!r}", flush=True)
        if _is_yes(answer):
            return True
        if _NO_RE.search(answer or ""):
            return False   # a clear no — don't badger
        # Silence or gibberish is NOT a refusal: the old code declined instantly,
        # so a moment's hesitation silently killed the action while Jarvis went
        # on to imply it had happened. Ask once more before giving up.
        answer = self._ask_once("Sorry sir — yes or no?")
        print(f"[confirm] retry heard {answer!r}", flush=True)
        return _is_yes(answer)

    # --- reminders / timers announce themselves --------------------------
    def _announce(self, text: str) -> bool:
        """Speak an unprompted notice. False = couldn't right now (muted/busy),
        so the caller may retry later."""
        if STATE["muted"] or not self._busy.acquire(blocking=False):
            return False  # muted or mid-conversation
        try:
            self._cancel.clear()   # a past barge-in must not gag announcements
            self._say(text)   # _say handles pausing the mic while speaking
            return True
        finally:
            self._busy.release()

    _DRAFT_PROMPT = (
        "You draft one short text-message reply on behalf of the user. Match the "
        "language of the incoming MESSAGE TEXT itself — Greek text gets a Greek "
        "reply, English text gets an ENGLISH reply, regardless of the sender's "
        "name. In Greek always use the INFORMAL SINGULAR (ενικός: εσύ/σου/σε), "
        "never the formal plural (εσείς/σας). Be natural and brief — one "
        "sentence. Return ONLY the reply text, nothing else. If no sensible "
        "reply exists, return exactly SKIP."
    )

    def _triage(self, sender: str, handle: str, body: str) -> None:
        """A known contact asked something — draft a reply and offer to send it.
        The spoken 'yes' here IS the send confirmation."""
        import re as _re
        from jarvis import provider
        from jarvis.tools.messages import _send_to_handle

        if not (_re.search(r"[?;]\s*$", body.strip()) or "?" in body):
            return   # not a question — announcing it was enough
        if not self._busy.acquire(blocking=False):
            return
        try:
            draft = ""
            try:
                for ev in provider.stream(
                    [{"role": "user", "content": f"Incoming text from {sender}: {body}"}],
                    self._DRAFT_PROMPT, [],
                ):
                    if ev["type"] == "text":
                        draft += ev["text"]
            except Exception:
                return
            draft = draft.strip().strip('"')
            if not draft or draft.upper() == "SKIP" or len(draft) > 200:
                return
            answer = self._ask_once(f"Shall I reply: {draft}?")
            if _is_yes(answer):
                r = _send_to_handle(handle, draft)
                self._say("Sent, sir." if r == "OK" else "It didn't go through, sir.")
        finally:
            self._busy.release()

    def _on_incoming_message(self, handle: str, body: str) -> bool:
        """A new text arrived: say who it's from and read it — Greek messages
        are read in actual Greek (the speaker picks the Greek voice)."""
        from jarvis.tools.messages import _handle_to_name
        from jarvis.voice import speech

        sender = _handle_to_name(handle)
        body_voice = None
        # A digits-only message ("8:30") has no language of its own — read it
        # in the language of the conversation (the latest message with words).
        if not re.search(r"[A-Za-zΆ-Ͽ]", body):
            try:
                from jarvis import msgwatch
                ctx = msgwatch.read_recent(count=8, handle_like=handle)
                for _h, b, _mine in reversed(ctx):   # newest first
                    if speech._GREEK_CHARS.search(b):
                        body_voice = speech.GREEK_VOICE
                        break
                    if re.search(r"[A-Za-z]", b):
                        break   # conversation is in English — default voice
            except Exception:
                pass
        ok = self._announce_message(sender, body, body_voice)
        # Triage: only for real contacts (a resolved name, not a raw number),
        # and only after the announcement actually happened.
        if ok and sender != handle:
            self._triage(sender, handle, body)
        return ok

    def _start_day_core(self) -> None:
        """The 'start the day' routine body — caller must hold the turn lock."""
        from jarvis.routines import start_day

        print("you> [routine] start the day", flush=True)
        if STATE["recording"]:
            transcript.log("You", "start the day")
        self._set("thinking")
        # He announces "Your day is started, sir" OVER the music; the routine
        # deducts the speaking time so the song still stops at 10.550s.
        start_day(cancel=self._cancel, say=self._say)

    def _start_day_routine(self) -> None:
        """Entry for the wake-phrase trigger (runs on its own thread)."""
        if not self._busy.acquire(timeout=10):
            return
        try:
            self._cancel.clear()
            self._start_day_core()
        finally:
            if not STATE["muted"]:
                self._set("listening")
            self._busy.release()

    def _announce_message(self, sender: str, body: str, body_voice=None) -> bool:
        """Speak 'message from X' + the body (optionally forcing the body's
        voice) under ONE turn hold. False = muted/busy, retry later."""
        if STATE["muted"] or not self._busy.acquire(blocking=False):
            return False
        try:
            self._cancel.clear()
            if STATE["recording"]:
                transcript.log(sender, body)
            self._hold_music(True)    # duck the music so the message is clear
            self._set("speaking")
            try:
                self._speak_safely(f"Sir, a message from {sender}.")
                if not self._cancel.is_set():
                    # The message text is arbitrary — if it contains "Jarvis",
                    # _speak_safely deafens him so he doesn't answer himself.
                    self._speak_safely(body, voice=body_voice)
            finally:
                self._hold_music(False)   # …and pick the song back up
                self._set("muted" if STATE["muted"] else "listening")
            return True
        finally:
            self._busy.release()

    def _on_incoming_email(self, account: str, sender: str, subject: str) -> bool:
        """A new primary-inbox email arrived — say which account and who from."""
        if STATE["recording"]:
            transcript.log("Email", f"[{account}] from {sender}: {subject}")
        self._hold_music(True)     # quieten the music for the announcement
        try:
            return self._announce(
                f"Sir, a new email on your {account} account, from {sender}."
            )
        finally:
            self._hold_music(False)

    def _fda_notice(self) -> None:
        # Keep trying until he's free to say it — it's actionable information.
        for _ in range(20):
            if self._announce(
                "By the way, sir — I can't see your incoming messages until you "
                "grant me Full Disk Access in System Settings, Privacy and Security."
            ):
                return
            time.sleep(30)

    def _on_reminder(self, notice: dict) -> None:  # heartbeat callback
        self._announce(notice["text"])

    # --- lifecycle -------------------------------------------------------
    def _quit(self) -> None:
        print("Shutting down.", flush=True)
        self.wake.stop()
        self.heartbeat.stop()
        if self._window_proc is not None:
            try:
                self._window_proc.terminate()
            except Exception:
                pass
        os._exit(0)

    def run(self) -> None:
        global _engine
        if _already_running():
            print("Jarvis is already running — exiting this instance.", flush=True)
            os._exit(0)
        _engine = self

        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self._url = f"http://127.0.0.1:{port}/"
        try:
            with open(_URLFILE, "w") as fh:
                fh.write(self._url)
        except Exception:
            pass
        self._open_window(self._url)

        print(f"Jarvis online — window {self._url}, listening for '{self.wake.phrase}'.", flush=True)
        self._boost_mic()   # crank input gain so you don't have to shout
        # Load the local Whisper model in the background so the first command
        # doesn't pay the one-time load cost.
        threading.Thread(target=stt.warm, daemon=True).start()
        threading.Thread(target=audio.warm_vad, daemon=True).start()
        # Local brain: make sure the Ollama server is up (no-op if not installed).
        def _brain_up():
            try:
                from jarvis import provider_local
                provider_local.ensure_server()
                # Prime with the REAL system prompt + tools so the prefix cache
                # is hot and the first command isn't a ~50s cold load.
                provider_local.warm(self.brain.system_prompt,
                                    self.brain.registry.schema())
            except Exception:
                pass
        threading.Thread(target=_brain_up, daemon=True).start()
        # Start the mic FIRST. The greeting and news are spoken by _news_briefing
        # once it's live, so you can cut him off from the very first word.
        self.wake.start()
        self.heartbeat.start()
        # Hotkeys (⌃⌥J / ⌃⌥Q) live in the separate always-on hotkey daemon
        # (jarvis/hotkeyd.py) so ⌃⌥J can relaunch Jarvis even after he's quit.
        threading.Thread(target=self._news_briefing, daemon=True).start()
        # Watch for incoming texts and read them out (Greek read as Greek).
        from jarvis.msgwatch import MessagesWatcher
        MessagesWatcher(
            on_message=self._on_incoming_message,
            on_denied=lambda: threading.Thread(target=self._fda_notice, daemon=True).start(),
        ).start()
        # Watch Gmail (primary inbox only) and announce new mail.
        from jarvis.gmailwatch import GmailWatcher
        GmailWatcher(on_email=self._on_incoming_email).start()
        # Pre-warm the Contacts directory (≈6s) so the first "text Alex" and
        # the first incoming-message announcement don't stall on it.
        def _warm():
            try:
                from jarvis.tools.messages import _contacts_directory
                _contacts_directory()
            except Exception:
                pass
        threading.Thread(target=_warm, daemon=True).start()
        # Runs forever in the background — closing the window does NOT stop it.
        try:
            while not lifecycle.shutdown_requested():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self._quit()

    def _news_briefing(self) -> None:
        from datetime import date
        from jarvis import news

        # The news runs ONCE a day — the first time Jarvis starts that day —
        # not on every restart. The stamp file survives restarts.
        stamp = os.path.join(_DATA, "news_last.txt")
        today = date.today().isoformat()
        try:
            already = open(stamp).read().strip() == today
        except Exception:
            already = False

        text = ""
        if not already:
            # Fetch while vosk is still loading — the two waits overlap.
            try:
                text = news.briefing(6)
            except Exception:
                text = "I couldn't reach the news just now, sir."
        # Don't speak until the mic is actually open: vosk takes a couple of
        # seconds to load, and anything said before then can't be interrupted.
        self.wake.wait_until_listening()
        # Hold the turn lock so a barge-in waits for us to bail out cleanly
        # rather than racing us to clear the cancel flag.
        if not self._busy.acquire(timeout=30):
            return
        try:
            if already:
                self._say("I am awake, sir.")
                return
            self._say("I am awake, sir. Let me go through today's news.")
            try:
                with open(stamp, "w") as fh:
                    fh.write(today)
            except Exception:
                pass
            self._say(text)   # _say no-ops if you already cut him off
        finally:
            self._busy.release()

    @staticmethod
    def _boost_mic() -> None:
        try:
            subprocess.run(
                ["osascript", "-e", "set volume input volume 100"],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass

    def _ensure_window(self) -> None:
        if self._window_proc is None or self._window_proc.poll() is not None:
            self._open_window(getattr(self, "_url", ""))

    def _quit_completely(self) -> None:
        """Fully stop Jarvis (voice 'goodbye' or ⌃⌥Q). Stays closed until you
        relaunch — double-click Jarvis.app or next login."""
        print("Quitting completely.", flush=True)
        try:
            self.speaker.stop()
        except Exception:
            pass
        self.wake.stop()
        self.heartbeat.stop()
        self._close_window()
        for f in (_LOCK, _URLFILE):
            try:
                os.remove(f)
            except Exception:
                pass
        os._exit(0)

    def _close_window(self) -> None:
        if self._window_proc is not None and self._window_proc.poll() is None:
            try:
                self._window_proc.terminate()
            except Exception:
                pass
        self._window_proc = None


    def _open_window(self, url: str) -> None:
        """Spawn the native circle window as a separate process (so its webview
        doesn't fight the mic for CoreAudio)."""
        try:
            self._window_proc = subprocess.Popen(
                [sys.executable, "-m", "jarvis", "--window", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            print(f"[couldn't open window: {exc}]", flush=True)


_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
_LOCK = os.path.join(_DATA, "jarvis.pid")
_URLFILE = os.path.join(_DATA, "jarvis.url")


def _already_running() -> bool:
    """True if another Jarvis instance is alive. If so, ask that instance to
    pop its window up (so double-clicking the app re-shows it) before exiting."""
    try:
        with open(_LOCK) as fh:
            pid = int(fh.read().strip())
        os.kill(pid, 0)  # raises if not alive
        # It's running — tell it to show its window, then we exit.
        try:
            import urllib.request
            url = open(_URLFILE).read().strip()
            urllib.request.urlopen(url + "show", data=b"", timeout=2)
        except Exception:
            pass
        return True
    except Exception:
        pass
    try:
        os.makedirs(_DATA, exist_ok=True)
        with open(_LOCK, "w") as fh:
            fh.write(str(os.getpid()))
    except Exception:
        pass
    return False


def run() -> None:
    JarvisEngine().run()
