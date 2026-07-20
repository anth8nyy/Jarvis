"""Audit trail + cost tally — the visible record of what Jarvis did and why.

A plain, human-readable log (data/audit.log): every tool run, every
confirmation asked, everything the heartbeat surfaced. When something
surprises you, this is where you look. A running model-cost tally
(data/cost.json) makes a runaway loop visible immediately.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from jarvis import appconfig

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
_LOG_PATH = os.path.join(_DATA_DIR, "audit.log")
_COST_PATH = os.path.join(_DATA_DIR, "cost.json")


def log(event: str, **details: Any) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    stamp = datetime.now().isoformat(timespec="seconds")
    parts = " ".join(f"{k}={v}" for k, v in details.items())
    line = f"{stamp}  {event}  {parts}".rstrip()
    with open(_LOG_PATH, "a") as fh:
        fh.write(line + "\n")


def _load_cost() -> dict:
    try:
        with open(_COST_PATH, "r") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {"input_tokens": 0, "output_tokens": 0, "usd": 0.0}


def record_usage(model: str, input_tokens: int, output_tokens: int) -> None:
    """Add one model call's token usage to the running tally."""
    pricing = appconfig.load()["pricing"]
    cost = _load_cost()
    cost["input_tokens"] += input_tokens
    cost["output_tokens"] += output_tokens
    cost["usd"] += (
        input_tokens / 1_000_000 * pricing["input_per_mtok"]
        + output_tokens / 1_000_000 * pricing["output_per_mtok"]
    )
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(_COST_PATH, "w") as fh:
        json.dump(cost, fh, indent=2)
    log("usage", model=model, in_tokens=input_tokens, out_tokens=output_tokens)


def cost_summary() -> str:
    cost = _load_cost()
    return (
        f"${cost['usd']:.4f} so far "
        f"({cost['input_tokens']} in / {cost['output_tokens']} out tokens)"
    )
