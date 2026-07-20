"""The voice frontend — a thin adapter around the SAME Brain the text CLI
uses. Voice only changes how a turn arrives (transcribed speech) and how it
leaves (spoken aloud); the brain in the middle is untouched.
"""

from __future__ import annotations

import re

from jarvis.brain import Brain
from jarvis.gate import console_confirmer
from jarvis.voice import audio, stt
from jarvis.voice.speech import Speaker

# Flush a chunk to speech once we've got a full sentence, so Jarvis can start
# speaking before the reply is finished being written.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def run() -> None:
    brain = Brain(confirmer=console_confirmer)
    speaker = Speaker()
    print("Jarvis's up (voice). Press Enter to talk; press Enter again to send.")
    print("Press Enter while she's talking to cut her off. Ctrl-C to quit.\n")

    while True:
        try:
            input("[Enter to speak] ")
        except (EOFError, KeyboardInterrupt):
            print("\nsee ya.")
            return

        # Any keypress to start a turn also barges in on anything still playing.
        speaker.stop()

        print("🎙  recording — press Enter to stop.")
        wav = audio.record_until_enter()

        try:
            transcript = stt.transcribe(wav)
        except stt.STTError as exc:
            print(f"  [couldn't hear you: {exc}]\n")
            continue
        if not transcript:
            print("  [heard nothing — try again]\n")
            continue

        # Show what Jarvis *thought* you said, so a wrong answer is easy to diagnose.
        print(f"you (heard)> {transcript}")

        print("jarvis> ", end="", flush=True)
        buffer = ""
        for event in brain.turn(transcript):
            if event["type"] == "text":
                print(event["text"], end="", flush=True)
                buffer += event["text"]
                # Flush any complete sentences to speech as they land.
                parts = _SENTENCE_END.split(buffer)
                if len(parts) > 1:
                    for sentence in parts[:-1]:
                        speaker.speak(sentence)
                    buffer = parts[-1]
            elif event["type"] == "tool":
                print(f"\n  [using {event['name']}…]\n", end="", flush=True)
            elif event["type"] == "error":
                print(f"[{event['text']}]", end="", flush=True)
        if buffer.strip():
            speaker.speak(buffer)
        print("\n")
