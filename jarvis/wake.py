"""Offline wake-word listener.

Continuously listens to the mic with vosk (runs locally, no network, no API
credits) and fires a callback when it hears the wake phrase. This is the
open-mic layer that sits on top of everything else — the eventual end goal
from AGENT.md's input roadmap.
"""

from __future__ import annotations

import audioop
import difflib
import json
import threading
import time
from collections import deque
from typing import Callable

import sounddevice as sd

from jarvis import config
from jarvis.voice import audio


class WakeListener:
    def __init__(
        self,
        on_wake: Callable[[bytes], None],
        phrase: str | None = None,
        on_interrupt: Callable[[], None] | None = None,
        is_speaking: Callable[[], bool] | None = None,
        routine_phrases: dict | None = None,
    ):
        # on_wake receives the command audio (WAV bytes) captured right after the
        # wake word. on_interrupt fires the INSTANT the wake word is heard (before
        # the command is even captured) so Jarvis can stop talking immediately.
        # is_speaking tells the matcher whether Jarvis is talking right now —
        # it changes what counts as a wake (see _matches).
        # routine_phrases: {phrase-or-compiled-regex: callback} — spoken phrases
        # that fire directly (with "jarvis" anywhere in the utterance, even at
        # the END, where normal position-aware waking would ignore it).
        self.on_wake = on_wake
        self.on_interrupt = on_interrupt
        self.is_speaking = is_speaking
        self.routine_phrases = routine_phrases or {}
        # A phrase heard WITHOUT the name ("let's start the day…") is held here
        # briefly, so a slightly-late "…Jarvis" still completes the routine.
        self._pending_routine = None  # (callback, heard_at)
        # Loudness-spike barge-in (speakers, no wake word needed): while Jarvis
        # speaks, his own echo sets the baseline; talking OVER him is a
        # sustained spike. True = this wake came from a spike, not the name.
        self.spike_wake = False
        self._echo_avg: float | None = None
        self._spike_run = 0
        self._speak_blocks = 0
        self.phrase = (phrase or config.WAKE_PHRASE).lower()
        self._stop = threading.Event()
        self._paused = threading.Event()
        # Set once the mic is actually open and matching. Loading the vosk model
        # takes a couple of seconds, so callers who must not speak un-interruptibly
        # (e.g. the startup news) wait on this first.
        self.ready = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def wait_until_listening(self, timeout: float = 20.0) -> bool:
        """Block until the mic is open (model loaded). True if it came up."""
        return self.ready.wait(timeout)

    def stop(self) -> None:
        self._stop.set()

    def pause(self) -> None:
        # Stop listening while Jarvis is recording/speaking (don't hear herself).
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def _run(self) -> None:
        from vosk import KaldiRecognizer, Model, SetLogLevel

        SetLogLevel(-1)  # quiet the model-loading log spam
        model = Model(config.WAKE_MODEL_DIR)
        sr = audio.input_rate()

        # Outer loop lets pause() fully close the mic stream, freeing the device
        # so the command recorder can use it, then reopen on resume.
        while not self._stop.is_set():
            if self._paused.is_set():
                time.sleep(0.1)
                continue

            rec = KaldiRecognizer(model, sr)
            block = max(1, sr // 10)  # ~100 ms per read
            # Blocking-read mode (not callback) — callback-mode CoreAudio streams
            # fail with a -50 when opened off the main thread.
            try:
                stream = sd.RawInputStream(
                    samplerate=sr, channels=1, dtype="int16", blocksize=block
                )
                stream.start()
            except Exception as exc:
                print(f"[wake: couldn't open mic, retrying: {exc}]")
                time.sleep(1.0)
                continue
            print(f"[wake] mic open, listening for '{self.phrase}' (sr={sr})", flush=True)
            self.ready.set()   # anyone waiting to speak interruptibly can go now
            # ~0.9s pre-roll so a command spoken right after the wake word (one
            # breath: "Jarvis open Spotify") isn't clipped.
            preroll = deque(maxlen=9)
            command_wav = None
            try:
                # Phase 1: wait for the wake word.
                woke = False
                while not self._stop.is_set() and not self._paused.is_set():
                    data, _ = stream.read(block)
                    data = bytes(data)
                    preroll.append(data)
                    if rec.AcceptWaveform(data):
                        heard = json.loads(rec.Result()).get("text", "")
                    else:
                        heard = json.loads(rec.PartialResult()).get("partial", "")
                    if self._spike_check(data):
                        # User is talking over Jarvis — cut him off and treat
                        # what they're saying as the command (preroll keeps it).
                        if self.on_interrupt:
                            try:
                                self.on_interrupt()
                            except Exception:
                                pass
                        self.spike_wake = True
                        woke = True
                        break
                    if self._routine_hit(heard):
                        break   # routine fired — no command capture; recognizer resets
                    if self._matches(heard):
                        # "let's start the day … Jarvis": the phrase finalized as
                        # its own utterance moments ago — this bare name completes
                        # the routine rather than starting a fresh command.
                        pending = self._consume_pending_routine()
                        if pending is not None:
                            threading.Thread(target=pending, daemon=True).start()
                            break
                        print("[wake] MATCH — capturing command", flush=True)
                        # Cut off any current speech the instant we hear the name.
                        if self.on_interrupt:
                            try:
                                self.on_interrupt()
                            except Exception:
                                pass
                        woke = True
                        break

                # Phase 2: keep capturing on the SAME stream until the user
                # finishes speaking. Nothing is lost in a stream handoff.
                if woke:
                    frames = list(preroll)
                    silent_for = 0
                    started = False
                    t0 = time.time()
                    while time.time() - t0 < 12.0:
                        data, _ = stream.read(block)
                        data = bytes(data)
                        frames.append(data)
                        if audioop.rms(data, 2) > 130:   # sensitive: normal speech triggers
                            started = True
                            silent_for = 0
                        elif started:
                            silent_for += 100
                        if started and silent_for >= 450:  # end-of-speech (don't cut early)
                            break
                        if not started and time.time() - t0 > 3.5:
                            break  # nothing after the wake word
                    pcm = b"".join(frames)
                    out_sr = sr
                    try:
                        # Peak-normalize: boost quiet speech toward full scale
                        # WITHOUT clipping (fixed gain distorted louder speech).
                        peak = audioop.max(pcm, 2)
                        if peak > 0:
                            gain = min(10.0, 0.9 * 32767 / peak)
                            if gain > 1.05:
                                pcm = audioop.mul(pcm, 2, gain)
                        pcm, _ = audioop.ratecv(pcm, 2, 1, sr, 16000, None)  # 16k = faster upload
                        out_sr = 16000
                    except Exception:
                        pass
                    command_wav = audio._pcm_to_wav(pcm, out_sr)
            finally:
                stream.stop()
                stream.close()

            # Process the command on a BACKGROUND thread so this loop can
            # immediately reopen the mic and keep listening while Jarvis thinks.
            # The engine pauses us (via wake.pause) only during actual speaking.
            if command_wav is not None:
                threading.Thread(
                    target=self.on_wake, args=(command_wav,), daemon=True
                ).start()

    @staticmethod
    def _jarvisish(heard: str) -> bool:
        """True if the name (or a close vosk mishearing of it) is in there."""
        if "jarvis" in heard.replace(" ", ""):
            return True
        # 0.65 admits near-misses like "travis"/"jervis" — safe here because a
        # routine ALSO needs its full phrase in the same utterance.
        return any(
            difflib.SequenceMatcher(None, w, "jarvis").ratio() >= 0.65
            for w in heard.split()
        )

    def _spike_check(self, data: bytes) -> bool:
        """True when the user talks OVER Jarvis loudly enough to interrupt.

        Only active while he speaks. His own echo forms a rolling baseline; a
        spike ≥3.5x baseline (and loud in absolute terms) sustained ~0.3s is a
        human, not room noise. Conservative on purpose — the vosk "Jarvis"
        barge-in still exists for quieter interruptions.
        """
        from jarvis import config
        if not config.SPEAKER_BARGE_IN:
            return False   # disabled → only the wake word interrupts him
        if self.is_speaking is None or not self.is_speaking():
            self._echo_avg = None
            self._spike_run = 0
            self._speak_blocks = 0
            return False
        r = float(audioop.rms(data, 2))
        self._speak_blocks += 1
        if self._echo_avg is None:
            self._echo_avg = max(r, 1.0)
        elif self._speak_blocks <= 10 and r > self._echo_avg:
            # During warm-up ONLY, rise fast: his own voice must become the
            # baseline before detection arms, or the quiet gap while the say-
            # process launches makes his speech look like a spike and he
            # interrupts himself mid-sentence. After warm-up the baseline stays
            # slow, so a genuine shout can't teach itself into the baseline.
            self._echo_avg = 0.5 * self._echo_avg + 0.5 * r
        # Never fire in the first ~1s of an utterance — the learning window.
        if self._speak_blocks <= 10:
            self._spike_run = 0
            return False
        if r > max(3.5 * self._echo_avg, 1500.0):
            self._spike_run += 1
        else:
            self._spike_run = 0
            self._echo_avg = 0.85 * self._echo_avg + 0.15 * r
        return self._spike_run >= 4

    def _routine_hit(self, heard: str) -> bool:
        """Fire a routine when its phrase AND the name are both heard — the name
        may sit anywhere, including the end ("let's start the day, Jarvis").
        A phrase heard without the name is remembered briefly (see
        _consume_pending_routine) so a pause before "Jarvis" still counts."""
        if not self.routine_phrases or not heard:
            return False
        h = heard.lower()
        matched = None
        for phrase, callback in self.routine_phrases.items():
            hit = phrase.search(h) if hasattr(phrase, "search") else phrase in h
            if hit:
                matched = callback
                break
        if matched is None:
            return False
        if self._jarvisish(h):
            self._pending_routine = None
            threading.Thread(target=matched, daemon=True).start()
            return True
        self._pending_routine = (matched, time.time())
        return False

    def _consume_pending_routine(self):
        """The routine whose phrase was heard in the last few seconds, if any —
        claimed by the wake path when a bare 'Jarvis' follows the phrase."""
        pending, self._pending_routine = self._pending_routine, None
        if pending and time.time() - pending[1] <= 3.5:
            return pending[0]
        return None

    @staticmethod
    def _sounds_like_jarvis(word: str) -> bool:
        """One word is the wake name, allowing for vosk's near-misses (jervis,
        jarvise…). 0.8 catches those but not real names like 'Travis' (0.67)."""
        return ("jarvis" in word
                or difflib.SequenceMatcher(None, word, "jarvis").ratio() >= 0.8)

    def _matches(self, heard: str) -> bool:
        heard = heard.lower().strip()
        if not heard:
            return False
        words = heard.split()
        joined = "".join(words)          # catches a split "jar vis" → "jarvis"

        # While Jarvis is talking, "Jarvis" ANYWHERE means "shut up" (barge-in) —
        # his own speech leaks into the partial, so position is meaningless.
        if self.is_speaking is not None and self.is_speaking():
            return "jarvis" in joined or any(self._sounds_like_jarvis(w) for w in words)

        # A SHORT utterance that contains the name is almost certainly addressed
        # to him ("Jarvis", "hey Jarvis", "Jarvis what's the weather") — wake on
        # the name anywhere. This is the common case and must be reliable.
        if len(words) <= 5:
            return "jarvis" in joined or any(self._sounds_like_jarvis(w) for w in words)

        # In a LONG utterance the name only wakes him if it's near the START
        # ("Jarvis, <long request>"). A name buried deep in a long sentence —
        # e.g. talking about him to someone else — is ignored.
        head = words[:3]
        return "jarvis" in "".join(head) or any(self._sounds_like_jarvis(w) for w in head)
