"""The tool registry — Jarvis's hands.

Every capability is a self-contained Tool registered here. Adding a new
capability means writing one handler and registering it; the agent loop in
brain.py never changes. The whole registry's schema is handed to the model
each turn so it knows what's available.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Tuple


@dataclass
class Tool:
    name: str
    description: str
    # JSON schema for the tool's inputs, as the model expects it.
    input_schema: Dict[str, Any]
    handler: Callable[..., str]
    # Tier 6 will gate these behind an explicit confirmation before running.
    # Recorded now so the flag has teeth the moment the gate is built.
    requires_confirmation: bool = False


class Registry:
    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def needs_confirmation(self, name: str) -> bool:
        """True if this tool must get an explicit yes before running — either
        flagged in code or listed in config.json's confirm_tools."""
        from jarvis import appconfig

        tool = self._tools.get(name)
        if tool is None:
            return False
        if tool.requires_confirmation:
            return True
        return name in appconfig.load().get("confirm_tools", [])

    def schema(self) -> List[Dict[str, Any]]:
        """The tool list handed to the model each turn."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in self._tools.values()
        ]

    def run(self, name: str, tool_input: Dict[str, Any]) -> Tuple[str, bool]:
        """Run a tool by name. Returns (result_text, is_error).

        A missing tool or a raised exception becomes a plain-language error
        string returned *to the model* — never a crash. The model reasons
        over the failure and decides how to recover or explain it.
        """
        tool = self._tools.get(name)
        if tool is None:
            return (f"No tool named '{name}' exists.", True)
        try:
            return (tool.handler(**tool_input), False)
        except TypeError as exc:
            # Wrong/missing arguments from the model.
            return (f"Tool '{name}' got bad inputs: {exc}", True)
        except Exception as exc:
            return (f"Tool '{name}' failed: {exc}", True)
