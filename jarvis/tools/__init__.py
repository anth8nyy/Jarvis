"""Assembles the tool registry. To add a capability: write a tool module with
a `register(registry)` function and call it here — nothing else changes.
"""

from jarvis import memory, notices
from jarvis.registry import Registry
from jarvis.tools import (
    calendar,
    calls,
    datetime_tool,
    day,
    mac,
    mail,
    messages,
    notes,
    spotify_play,
    system,
    tasks,
    vision,
    weather,
    websearch,
)


def build_registry() -> Registry:
    registry = Registry()
    tasks.register(registry)
    mac.register(registry)
    mail.register(registry)
    memory.register(registry)
    notices.register(registry)
    datetime_tool.register(registry)
    calendar.register(registry)
    calls.register(registry)
    spotify_play.register(registry)
    websearch.register(registry)
    messages.register(registry)
    weather.register(registry)
    system.register(registry)
    vision.register(registry)
    notes.register(registry)
    day.register(registry)
    return registry
