"""The confirmation gate — how Jarvis asks before doing something consequential.

A confirmer takes a request describing the action and returns True to allow.
The gate sits between the model choosing a tool and the tool running, so it
covers typed, spoken, and heartbeat-initiated actions alike.

  console_confirmer — asks the user in the terminal and waits for a yes.
  deny_confirmer    — the safe default: never approves. Used when there's no
                      human to ask (e.g. a background action with nobody
                      present), so the loop never blocks forever waiting.
"""

from __future__ import annotations

from typing import Any, Dict


def console_confirmer(request: Dict[str, Any]) -> bool:
    print(f"\n⚠️  Jarvis wants to run '{request['name']}': {request.get('description', '')}")
    print(f"    inputs: {request.get('input')}")
    answer = input("    Allow this? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def deny_confirmer(request: Dict[str, Any]) -> bool:
    # Safe default: no human to ask → do nothing.
    return False
