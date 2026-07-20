"""Eyes: capture the screen so the brain can SEE it.

The handler returns a marker string ("IMAGE_FILE:<path>") — brain.py converts
that into an actual image content block for the model, because tool handlers
themselves only return text. Requires the Screen Recording permission; without
it macOS hands back a wallpaper-only shot, which we detect and report honestly.
"""

from __future__ import annotations

import os
import subprocess
import tempfile

from jarvis.registry import Registry, Tool

IMAGE_MARKER = "IMAGE_FILE:"


def look_at_screen() -> str:
    path = os.path.join(tempfile.gettempdir(), "jarvis_screen.jpg")
    r = subprocess.run(
        ["screencapture", "-x", "-t", "jpg", path],
        capture_output=True, text=True, timeout=15,
    )
    if r.returncode != 0 or not os.path.exists(path):
        return ("I couldn't capture the screen, sir — grant me Screen Recording "
                "access in System Settings, Privacy and Security.")
    # Retina shots are huge; halve to keep the upload fast and cheap.
    subprocess.run(["sips", "--resampleWidth", "1470", path],
                   capture_output=True, timeout=15)
    if os.path.getsize(path) < 30_000:
        # A near-empty JPEG = wallpaper-only capture = no permission.
        return ("The capture came back blank, sir — macOS is blocking me. Grant "
                "Screen Recording in System Settings, Privacy and Security, "
                "then relaunch me.")
    return IMAGE_MARKER + path


def register(registry: Registry) -> None:
    registry.register(Tool(
        name="look_at_screen",
        description=(
            "Take a screenshot and SEE the user's screen. Use whenever they ask "
            "about something they're looking at: 'what's this error', 'what's on "
            "my screen', 'read this page', 'summarise this', 'translate what I'm "
            "seeing'. After looking, answer their actual question about what's "
            "visible — concisely, spoken aloud."
        ),
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=look_at_screen,
    ))
