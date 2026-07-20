"""Time and date awareness."""

from __future__ import annotations

from datetime import datetime

from jarvis.registry import Registry, Tool


def get_datetime() -> str:
    now = datetime.now()
    return now.strftime("It's %A, %B %-d, %Y, %-I:%M %p.")


def register(registry: Registry) -> None:
    registry.register(
        Tool(
            name="get_datetime",
            description="Get the current local date and time. Use whenever the user asks what day/time it is, or when you need 'now' to compute a schedule or reminder.",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=get_datetime,
        )
    )
