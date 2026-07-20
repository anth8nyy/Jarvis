"""Reminders & tasks — a simple, durable to-do list.

Server-side state (per the architecture in AGENT.md): this runs wherever the
brain runs and persists to a plain JSON file the user can open and edit.
"""

import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List

from jarvis.registry import Registry, Tool

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
_TASKS_PATH = os.path.join(_DATA_DIR, "tasks.json")


def _load() -> List[Dict[str, Any]]:
    try:
        with open(_TASKS_PATH, "r") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return []


def _save(tasks: List[Dict[str, Any]]) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(_TASKS_PATH, "w") as fh:
        json.dump(tasks, fh, indent=2)


def _next_id(tasks: List[Dict[str, Any]]) -> int:
    return max((t["id"] for t in tasks), default=0) + 1


def add_task(text: str) -> str:
    tasks = _load()
    task = {"id": _next_id(tasks), "text": text, "done": False, "due": None, "notified": False}
    tasks.append(task)
    _save(tasks)
    return f"Added task #{task['id']}: {text}"


def remind_me(text: str, in_seconds: int) -> str:
    """Set a timer/reminder that fires exactly `in_seconds` from now."""
    from jarvis import lifecycle

    lifecycle.schedule_reminder(in_seconds, f"Reminder: {text}")
    if in_seconds < 60:
        when = f"{in_seconds} seconds"
    elif in_seconds % 60 == 0:
        m = in_seconds // 60
        when = f"{m} minute{'s' if m != 1 else ''}"
    else:
        when = f"{in_seconds // 60}m {in_seconds % 60}s"
    return f"Timer set for {when}, sir."


def pop_due_reminders() -> List[str]:
    """Return the text of reminders now due and unnotified, marking them
    notified so they surface exactly once. Called by the heartbeat check."""
    tasks = _load()
    now = datetime.now()
    due_texts: List[str] = []
    changed = False
    for task in tasks:
        if task.get("done") or task.get("notified") or not task.get("due"):
            continue
        if datetime.fromisoformat(task["due"]) <= now:
            due_texts.append(task["text"])
            task["notified"] = True
            changed = True
    if changed:
        _save(tasks)
    return due_texts


def list_tasks(include_done: bool = False) -> str:
    tasks = _load()
    if not include_done:
        tasks = [t for t in tasks if not t["done"]]
    if not tasks:
        return "No tasks."
    lines = [
        f"#{t['id']} [{'x' if t['done'] else ' '}] {t['text']}" for t in tasks
    ]
    return "\n".join(lines)


def complete_task(id: int) -> str:
    tasks = _load()
    for task in tasks:
        if task["id"] == id:
            task["done"] = True
            _save(tasks)
            return f"Marked task #{id} done: {task['text']}"
    return f"No task with id #{id}."


def delete_task(id: int) -> str:
    tasks = _load()
    for i, task in enumerate(tasks):
        if task["id"] == id:
            removed = tasks.pop(i)
            _save(tasks)
            return f"Deleted task #{id}: {removed['text']}"
    return f"No task with id #{id}."


def register(registry: Registry) -> None:
    registry.register(
        Tool(
            name="add_task",
            description="Add a reminder or to-do item to the user's task list. Use when the user asks to remember, note, or be reminded of something to do.",
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The task or reminder text."}
                },
                "required": ["text"],
            },
            handler=add_task,
        )
    )
    registry.register(
        Tool(
            name="remind_me",
            description="Set a timer or reminder that fires at an exact time. Use for 'remind me to X in N minutes', 'set a timer for N seconds/minutes', etc. Convert the duration to an EXACT number of seconds (1 minute = 60, 90 seconds = 90, 2 minutes = 120, 1 hour = 3600).",
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "What to remind the user about (for a bare timer, use 'time's up' or similar)."},
                    "in_seconds": {"type": "integer", "description": "Exact number of seconds from now until it fires."},
                },
                "required": ["text", "in_seconds"],
            },
            handler=remind_me,
        )
    )
    registry.register(
        Tool(
            name="list_tasks",
            description="List the user's current tasks. Use when the user asks what's on their list, what they need to do, or to review reminders.",
            input_schema={
                "type": "object",
                "properties": {
                    "include_done": {
                        "type": "boolean",
                        "description": "Include already-completed tasks. Defaults to false.",
                    }
                },
                "required": [],
            },
            handler=list_tasks,
        )
    )
    registry.register(
        Tool(
            name="complete_task",
            description="Mark a task as done by its id number. Use when the user says they finished or completed something.",
            input_schema={
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "The task's id number."}
                },
                "required": ["id"],
            },
            handler=complete_task,
        )
    )
    registry.register(
        Tool(
            name="delete_task",
            description="Permanently remove a task from the list by its id number. Use only when the user explicitly wants a task deleted, not merely completed.",
            input_schema={
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "The task's id number."}
                },
                "required": ["id"],
            },
            handler=delete_task,
            # Deleting data is on the user's "never without asking" list.
            requires_confirmation=True,
        )
    )
