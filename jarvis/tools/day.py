"""The morning routine, exposed as a tool.

The wake-phrase and fast-path regexes catch the exact wordings instantly, but
speech transcription mangles them. Giving the
brain a tool means ANY phrasing still reaches the routine — Claude judges the
intent instead of a regex demanding exact words.
"""

from __future__ import annotations

from jarvis.registry import Registry, Tool


def start_day() -> str:
    """Run the morning routine, speaking through the live engine if there is one."""
    from jarvis import routines

    engine = None
    try:
        from jarvis.app import desktop
        engine = desktop._engine
    except Exception:
        pass

    if engine is None:
        routines.start_day()   # text/CLI mode: no voice to speak through
        return "The morning routine has run."
    # say= lets the announcement land OVER the music, as it should.
    routines.start_day(cancel=engine._cancel, say=engine._say)
    return "ROUTINE_DONE: already spoken aloud to the user — add nothing further."


def register(registry: Registry) -> None:
    registry.register(
        Tool(
            name="start_day",
            description=(
                "Run the user's morning routine: opens Spotify and plays a song "
                "briefly, opens Claude (on Claude Code), ChatGPT and Notes, "
                "arranges the windows, then closes Spotify. He announces it "
                "himself while it runs.\n"
                "The user triggers this by saying \"let's start the day\" "
                "(speech is transcribed imperfectly, so it may arrive garbled, e.g. "
                "\"start of the day\"). Call this tool whenever the user asks to "
                "start/set up their day or run the morning routine."
            ),
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=start_day,
        )
    )
