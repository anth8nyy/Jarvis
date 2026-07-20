"""Read Gmail on request (the passive watcher lives in jarvis/gmailwatch.py)."""

from __future__ import annotations

from jarvis.registry import Registry, Tool


def read_emails() -> str:
    from jarvis import gmailwatch
    return gmailwatch.read_recent(limit=6)


def register(registry: Registry) -> None:
    registry.register(Tool(
        name="read_emails",
        description=(
            "Read the user's recent unread Gmail (real/primary mail only — never "
            "promotions or spam), across all their accounts, saying which account "
            "and who each is from. Use for 'any new emails?', 'check my email', "
            "'read my Gmail'."
        ),
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=read_emails,
    ))
