"""Speech-to-text seam. One job: give it audio (WAV bytes), get back text.

Prefers local Whisper (free, offline) and falls back to Deepgram (cloud). Swap
the provider here and nothing else in the voice loop changes.
"""

from __future__ import annotations

import requests

from jarvis import config

_URL = "https://api.deepgram.com/v1/listen"


class STTError(Exception):
    pass


def warm() -> None:
    """Pre-load the local model at startup if we're using it."""
    if config.STT_ENGINE == "whisper":
        from jarvis.voice import whisper_stt

        if whisper_stt.available():
            whisper_stt.warm()


def transcribe(wav_bytes: bytes) -> str:
    """Transcribe WAV audio to text via the configured engine."""
    if config.STT_ENGINE == "whisper":
        from jarvis.voice import whisper_stt

        if whisper_stt.available():
            try:
                return whisper_stt.transcribe(wav_bytes)
            except Exception as exc:
                # Local model broke — fall back to the cloud rather than go deaf.
                print(f"[whisper] failed, falling back to Deepgram: {exc}", flush=True)
    return _deepgram(wav_bytes)


def _params() -> list:
    """Query params as a LIST of pairs — `keyterm` is repeated once per term,
    which a dict can't express."""
    p = [
        ("model", config.STT_MODEL),
        ("smart_format", "true"),
        ("language", config.STT_LANGUAGE),
    ]
    # Keyterm prompting is nova-3 + English only; sending it elsewhere is
    # pointless at best, so only attach it where it actually applies.
    if config.STT_MODEL.startswith("nova-3") and config.STT_LANGUAGE == "en":
        p += [("keyterm", k) for k in config.STT_KEYTERMS]
    return p


def _deepgram(wav_bytes: bytes) -> str:
    """Cloud transcription via Deepgram (fallback)."""
    if not config.DEEPGRAM_API_KEY:
        raise STTError("DEEPGRAM_API_KEY is not set — can't transcribe.")
    try:
        resp = requests.post(
            _URL,
            params=_params(),
            headers={
                "Authorization": f"Token {config.DEEPGRAM_API_KEY}",
                "Content-Type": "audio/wav",
            },
            data=wav_bytes,
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        return (
            payload["results"]["channels"][0]["alternatives"][0]["transcript"].strip()
        )
    except requests.RequestException as exc:
        raise STTError(f"Deepgram request failed: {exc}") from exc
    except (KeyError, IndexError, ValueError) as exc:
        raise STTError(f"Unexpected Deepgram response: {exc}") from exc
