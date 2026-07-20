"""Text-to-speech via macOS `say` — free, offline, instant.

Replaces the paid ElevenLabs path. `say` plays audio directly (no network
round-trip, no PCM to pipe), which is a big latency win. Interruptible: a new
turn kills any in-progress speech so Jarvis never talks over you.
"""

from __future__ import annotations

import functools
import re
import subprocess
import threading

from jarvis import config

# Strip emoji / pictographs so `say` doesn't read them aloud ("flag France").
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\U00002190-\U000021FF️‍]"
)

# A `say -v '?'` line: "Daniel (Enhanced)   en_GB   # Hello! My name is Daniel."
_VOICE_LINE = re.compile(r"^(.+?)\s{2,}([a-z]{2}_[A-Z]{2})\s")
# Quality tiers — Premium and Enhanced are the downloadable neural-ish voices
# that actually sound human; a bare name is the small robotic compact voice.
_TIERS = {"premium": 3, "enhanced": 2, "": 1}


def _installed_voices() -> list[tuple[str, str]]:
    """[(name, locale)] for every voice `say` can use right now."""
    try:
        out = subprocess.run(
            ["say", "-v", "?"], capture_output=True, text=True, timeout=10
        ).stdout
    except Exception:
        return []
    voices = []
    for line in out.splitlines():
        m = _VOICE_LINE.match(line)
        if m:
            voices.append((m.group(1).strip(), m.group(2)))
    return voices


@functools.lru_cache(maxsize=1)
def pick_voice() -> str:
    """The best-sounding voice actually installed, honouring JARVIS_VOICE.

    Ranks by preferred family (config.VOICE_PREFERENCE) then quality tier, so
    downloading "Daniel (Enhanced)" is enough to upgrade him — no code change.
    """
    if config.JARVIS_VOICE:
        return config.JARVIS_VOICE   # explicit override wins

    best, best_rank = None, None
    for name, _loc in _installed_voices():
        m = re.match(r"^([A-Za-z]+)(?:\s*\((Premium|Enhanced)\))?$", name)
        if not m:
            continue
        family, tier = m.group(1), (m.group(2) or "").lower()
        if family not in config.VOICE_PREFERENCE:
            continue
        # Lower is better: earlier family, then higher tier.
        rank = (config.VOICE_PREFERENCE.index(family), -_TIERS.get(tier, 1))
        if best_rank is None or rank < best_rank:
            best, best_rank = name, rank
    return best or "Daniel"


# Greek letters (incl. polytonic). Text containing these is spoken with the
# Greek system voice so it sounds like actual Greek, not an English voice
# mangling the alphabet.
_GREEK_CHARS = re.compile(r"[Ͱ-Ͽἀ-῿]")
GREEK_VOICE = "Melina"


def _greek_voice_installed() -> bool:
    return any(n.startswith(GREEK_VOICE) for n, _ in _installed_voices())


_LATIN_CHARS = re.compile(r"[A-Za-z]")


def split_by_language(text: str) -> list:
    """[(voice, chunk)] — contiguous runs of Greek vs non-Greek words, so a
    mixed announcement like 'Message from Mum: Έλα σπίτι' is voiced right.

    Neutral tokens — numbers, times, punctuation — carry no language of their
    own, so they inherit the run they follow ('Θα έρθω στις 8:30' keeps 8:30
    in the GREEK voice, not a sudden English 'eight thirty')."""
    if not _GREEK_CHARS.search(text) or not _greek_voice_installed():
        return [(None, text)]
    words = text.split()
    # Classify each word: True=Greek, False=Latin, None=neutral (digits etc).
    kinds: list = []
    for w in words:
        if _GREEK_CHARS.search(w):
            kinds.append(True)
        elif _LATIN_CHARS.search(w):
            kinds.append(False)
        else:
            kinds.append(None)
    # Neutrals inherit the preceding run; leading neutrals join the first run.
    last = None
    for i, k in enumerate(kinds):
        if k is None:
            kinds[i] = last
        else:
            last = k
    nxt = last
    for i in range(len(kinds) - 1, -1, -1):
        if kinds[i] is None:
            kinds[i] = nxt
        else:
            nxt = kinds[i]
    segments: list = []
    for w, k in zip(words, kinds):
        if segments and segments[-1][0] == k:
            segments[-1][1].append(w)
        else:
            segments.append([k, [w]])
    return [(GREEK_VOICE if k else None, " ".join(ws)) for k, ws in segments]


class Speaker:
    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._interrupted = threading.Event()

    def speak(self, text: str, voice: str | None = None) -> None:
        """Speak text and block until finished (or interrupted). Greek runs are
        automatically voiced by the Greek system voice."""
        self._interrupted.clear()
        for seg_voice, chunk in split_by_language(text):
            if self._interrupted.is_set():
                return  # barge-in mid-announcement — drop the rest
            self._speak_one(chunk, voice or seg_voice or pick_voice())

    def _speak_one(self, text: str, voice: str) -> None:
        text = _EMOJI.sub("", text).strip()
        if not text:
            return
        with self._lock:
            if self._interrupted.is_set():
                return
            self._proc = subprocess.Popen(
                ["say", "-v", voice, "-r", str(config.JARVIS_SPEECH_RATE), text]
            )
            proc = self._proc
        proc.wait()
        with self._lock:
            if self._proc is proc:
                self._proc = None

    def stop(self) -> None:
        """Cut off whatever is being said right now (barge-in)."""
        self._interrupted.set()
        with self._lock:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
            self._proc = None

    @property
    def speaking(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None
