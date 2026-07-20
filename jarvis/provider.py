"""Thin seam around the model provider.

Nothing outside this module should import `anthropic` directly. If the
provider ever changes, this is the only file that needs to.
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List

import anthropic

from jarvis import config

_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


class ProviderError(Exception):
    """Raised when the model can't be reached or returns an error."""


def stream(
    messages: List[Dict[str, Any]],
    system: str,
    tools: List[Dict[str, Any]] | None = None,
) -> Iterator[Dict[str, Any]]:
    """Stream one model response.

    Yields events:
      {"type": "text", "text": <delta>}   as reply text streams in
      {"type": "final", "message": <msg>} once, at the end, carrying the
                                          full message (incl. any tool_use
                                          blocks and the stop_reason).
    """
    kwargs: Dict[str, Any] = {
        "model": config.MODEL,
        "max_tokens": config.MAX_TOKENS,
        # Cache the (stable) system prompt + tools so every turn after the first
        # reads the prefix from cache instead of re-processing it — faster and
        # cheaper.
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools

    try:
        with _client.messages.stream(**kwargs) as s:
            for text in s.text_stream:
                yield {"type": "text", "text": text}
            yield {"type": "final", "message": s.get_final_message()}
    except anthropic.APIError as exc:
        raise ProviderError(str(exc)) from exc
    except Exception as exc:
        raise ProviderError(str(exc)) from exc
