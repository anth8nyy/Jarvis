"""The native app window — a real macOS window (WKWebView) showing just the
reactor circle and a mute button.

Runs as its own process, separate from the mic engine. That separation is the
trick: an embedded webview and the microphone fight over CoreAudio in one
process (the -50 error); in different processes they don't. The window polls
the engine's local URL for state and posts mute toggles back.
"""

from __future__ import annotations

import webview


def run_window(url: str) -> None:
    webview.create_window(
        "J.A.R.V.I.S",
        url=url,
        width=680,
        height=680,
        background_color="#04070a",
        resizable=True,
    )
    webview.start()
