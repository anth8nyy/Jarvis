"""Local speech-to-text with faster-whisper — free, offline, no API.

Runs on the Mac itself (CTranslate2), so there's no network round-trip and no
per-request cost. The model loads once and stays resident; warm() kicks that
off in the background at startup so the first real command isn't slow.
"""

from __future__ import annotations

import io
import threading
import warnings
import wave

# faster-whisper's mel filter emits harmless numpy warnings on silent chunks;
# keep them out of the log so real errors stay visible.
warnings.filterwarnings("ignore", category=RuntimeWarning, module="faster_whisper.*")

from jarvis import config

_model = None
_lock = threading.Lock()


def _load():
    global _model
    with _lock:
        if _model is not None:
            return _model
        from faster_whisper import WhisperModel

        # int8 on CPU is the sweet spot on Apple Silicon: base.en does a short
        # command in ~0.3s. Model + compute type are configurable.
        _model = WhisperModel(
            config.WHISPER_MODEL,
            device="cpu",
            compute_type=config.WHISPER_COMPUTE,
            download_root=config.WHISPER_DIR,
        )
        return _model


def warm() -> None:
    """Load the model ahead of time (call at startup, off the main thread)."""
    try:
        _load()
    except Exception as exc:
        print(f"[whisper] warm failed: {exc}", flush=True)


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401

        return True
    except Exception:
        return False


def transcribe(wav_bytes: bytes) -> str:
    model = _load()
    # faster-whisper takes a path or a float array; decode the WAV to float32
    # in memory so we never touch disk.
    import numpy as np

    with wave.open(io.BytesIO(wav_bytes)) as w:
        frames = w.readframes(w.getnframes())
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

    segments, _info = model.transcribe(
        audio,
        language="en",
        beam_size=1,          # greedy: fastest, plenty for short commands
        vad_filter=False,     # our own VAD already trimmed silence
        # Bias the decoder toward the words Jarvis actually expects — the local
        # equivalent of Deepgram keyterms.
        initial_prompt=config.WHISPER_PROMPT,
    )
    return "".join(s.text for s in segments).strip()
