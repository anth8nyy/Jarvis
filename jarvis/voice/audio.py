"""Microphone capture and speaker playback.

Push-to-talk here is Enter-to-toggle (no OS permissions needed): recording
starts when you call record_until_enter and stops when you press Enter.
Playback is interruptible so Jarvis can be cut off mid-sentence (barge-in).
"""

from __future__ import annotations

import audioop
import io
import threading
import time
import wave
from typing import Iterator

import sounddevice as sd

SAMPLE_RATE_IN = 16000   # fallback record rate if the device rate can't be read
SAMPLE_RATE_OUT = 24000  # what TTS returns and we play
CHANNELS = 1
_DTYPE = "int16"


# ---- Voice-activity detection (Silero) --------------------------------------
# A real VAD instead of a loudness threshold: knows speech from keyboard
# clatter and doesn't cut the user off at a mid-sentence pause. Falls back to
# rms if the model can't load.
_vad = None
_vad_state = {"failed": False}
_vad_lock = threading.Lock()


def _get_vad():
    global _vad
    from jarvis import config
    if not config.USE_SILERO_VAD:
        return None   # disabled → callers fall back to the loudness threshold
    if _vad is not None or _vad_state["failed"]:
        return _vad
    with _vad_lock:
        if _vad is not None or _vad_state["failed"]:
            return _vad
        try:
            from silero_vad import load_silero_vad

            _vad = load_silero_vad(onnx=True)
        except Exception as exc:
            print(f"[vad] unavailable, using loudness fallback: {exc}", flush=True)
            _vad_state["failed"] = True
    return _vad


def warm_vad() -> None:
    """Load the VAD model ahead of time (startup, off the main thread)."""
    _get_vad()


def _block_is_speech(block: bytes, sr: int, threshold_rms: int, state: dict) -> bool:
    """True if this ~100ms block contains speech. Silero VAD when available
    (fed 512-sample windows at 16k), else the old rms threshold."""
    vad = _get_vad()
    if vad is None:
        return audioop.rms(block, 2) > threshold_rms
    try:
        import numpy as np
        import torch

        pcm = block
        if sr != 16000:
            pcm, state["rc"] = audioop.ratecv(pcm, 2, 1, sr, 16000, state.get("rc"))
        x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        best = 0.0
        for i in range(0, len(x) - 511, 512):
            p = float(vad(torch.from_numpy(x[i:i + 512]), 16000).item())
            if p > best:
                best = p
        return best >= 0.5
    except Exception:
        return audioop.rms(block, 2) > threshold_rms


def input_rate() -> int:
    """The mic's native sample rate. Recording at the device's own rate avoids
    a CoreAudio resampling path that errors (-50) on some Macs, especially when
    the stream is opened from a background thread."""
    try:
        return int(sd.query_devices(kind="input")["default_samplerate"])
    except Exception:
        return SAMPLE_RATE_IN


def record_until_enter() -> bytes:
    """Record from the mic until the user presses Enter. Returns WAV bytes."""
    frames: list[bytes] = []
    sr = input_rate()

    def callback(indata, _frames, _time, status):  # noqa: ANN001
        frames.append(bytes(indata))

    stream = sd.RawInputStream(
        samplerate=sr,
        channels=CHANNELS,
        dtype=_DTYPE,
        callback=callback,
    )
    with stream:
        input()  # blocks until Enter; recording runs in the callback meanwhile

    return _pcm_to_wav(b"".join(frames), sr)


def record_until_silence(
    max_seconds: float = 12.0,
    silence_ms: int = 450,
    threshold: int = 130,
    start_timeout: float = 6.0,
) -> bytes:
    """Record from the mic until the speaker pauses. For the GUI, where there's
    no Enter key: waits for speech to start, then stops after `silence_ms` of
    quiet. If no speech begins within `start_timeout`, returns empty so the
    caller can say "didn't catch that" quickly instead of hanging. WAV bytes."""
    sr = input_rate()
    block = max(1, sr // 10)  # ~100 ms per read
    frames: list[bytes] = []
    started = False
    silent_for = 0
    stream = sd.RawInputStream(
        samplerate=sr, channels=CHANNELS, dtype=_DTYPE, blocksize=block
    )
    stream.start()
    start_t = time.time()
    vst: dict = {}
    try:
        while time.time() - start_t < max_seconds:
            data, _ = stream.read(block)
            data = bytes(data)
            frames.append(data)
            loud = _block_is_speech(data, sr, threshold, vst)
            if loud:
                started = True
                silent_for = 0
            elif started:
                silent_for += 100
            if started and silent_for >= silence_ms:
                break
            if not started and time.time() - start_t > start_timeout:
                return b""  # they never spoke
    finally:
        stream.stop()
        stream.close()
    return _pcm_to_wav(b"".join(frames), sr)


def _pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(2)  # int16
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


class Player:
    """Plays streamed PCM, interruptibly. Call stop() from another thread to
    cut playback off between chunks (barge-in)."""

    def __init__(self) -> None:
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def play(self, pcm_chunks: Iterator[bytes]) -> None:
        self._stop.clear()
        stream = sd.RawOutputStream(
            samplerate=SAMPLE_RATE_OUT,
            channels=CHANNELS,
            dtype=_DTYPE,
        )
        stream.start()
        # A chunk may end mid-sample; sounddevice only accepts whole frames
        # (2 bytes for int16 mono). Carry the leftover byte into the next chunk.
        framesize = 2 * CHANNELS
        carry = b""
        try:
            for chunk in pcm_chunks:
                if self._stop.is_set():
                    break
                data = carry + chunk
                n = len(data) - (len(data) % framesize)
                if n:
                    stream.write(data[:n])
                carry = data[n:]
        finally:
            stream.stop()
            stream.close()
