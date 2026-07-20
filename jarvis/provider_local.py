"""Local brain via Ollama — fully offline, no API key, no bill.

Speaks the exact same event protocol as provider.stream() (text events + a
final message object with .content blocks, .stop_reason, .usage), so brain.py
doesn't know or care which brain is plugged in. Anthropic-style history/tools
are translated to Ollama's OpenAI-ish chat format on the way in, and the
response is wrapped back into block objects on the way out.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, Iterator, List

import requests

from jarvis import config


class ProviderError(Exception):
    pass


class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def model_dump(self):
        return dict(self.__dict__)


class _Msg:
    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = None


def _to_ollama(history: List[Dict], system: str) -> List[Dict]:
    # Map each tool_use id -> its tool name up front, so every tool_result can
    # be tagged with `tool_name`. WITHOUT that tag the model never registers the
    # result and re-calls the same tool forever (an infinite loop).
    id_to_name: Dict[str, str] = {}
    for m in history:
        if isinstance(m.get("content"), list):
            for b in m["content"]:
                if b.get("type") == "tool_use":
                    id_to_name[b.get("id", "")] = b.get("name", "")

    msgs = [{"role": "system", "content": system}]
    for m in history:
        role, content = m["role"], m["content"]
        if isinstance(content, str):
            msgs.append({"role": role, "content": content})
            continue
        text_parts, tool_calls, tool_results = [], [], []
        for b in content:
            t = b.get("type")
            if t == "text":
                text_parts.append(b.get("text", ""))
            elif t == "tool_use":
                tool_calls.append({
                    "id": b.get("id", ""),
                    "function": {"name": b["name"], "arguments": b.get("input") or {}}})
            elif t == "tool_result":
                c = b.get("content")
                if isinstance(c, list):   # image blocks etc → text only
                    c = " ".join(x.get("text", "[image]") for x in c if isinstance(x, dict))
                name = id_to_name.get(b.get("tool_use_id", ""), "")
                tool_results.append((name, str(c)))
        if role == "assistant":
            out: Dict[str, Any] = {"role": "assistant", "content": " ".join(text_parts)}
            if tool_calls:
                out["tool_calls"] = tool_calls
            msgs.append(out)
        elif tool_results:
            for name, r in tool_results:
                msg = {"role": "tool", "content": r}
                if name:
                    msg["tool_name"] = name   # THE fix — links result to its call
                msgs.append(msg)
        else:
            msgs.append({"role": "user", "content": " ".join(text_parts)})
    return msgs


def _to_ollama_tools(tools_schema: List[Dict]) -> List[Dict]:
    return [{
        "type": "function",
        "function": {
            "name": t["name"],
            "description": t.get("description", ""),
            "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
        },
    } for t in tools_schema]


def stream(history: List[Dict], system: str, tools_schema: List[Dict]) -> Iterator[Dict]:
    payload = {
        "model": config.LOCAL_MODEL,
        "messages": _to_ollama(history, system),
        "stream": True,
        # keep_alive keeps the model resident — WITHOUT it Ollama unloads after
        # 5 min idle and the next command pays a ~50s cold reload.
        "keep_alive": config.LOCAL_KEEPALIVE,
        "options": {"num_predict": config.MAX_TOKENS * 3, "temperature": 0.3},
    }
    if tools_schema:
        payload["tools"] = _to_ollama_tools(tools_schema)
    try:
        r = requests.post(f"{config.OLLAMA_URL}/api/chat", json=payload,
                          stream=True, timeout=(5, 180))
        r.raise_for_status()
    except requests.RequestException as exc:
        raise ProviderError(f"local brain unreachable: {exc}") from exc

    text = ""
    calls: List[Dict] = []
    for line in r.iter_lines():
        if not line:
            continue
        try:
            chunk = json.loads(line)
        except ValueError:
            continue
        msg = chunk.get("message") or {}
        piece = msg.get("content") or ""
        if piece:
            # DON'T stream text out yet: a small model may be typing a tool call
            # as text ("send_message(...)"), which must never be spoken aloud.
            # Buffer it and decide once the turn is complete.
            text += piece
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            calls.append({"name": fn.get("name", ""), "input": args})
        if chunk.get("done"):
            break

    # Small models sometimes WRITE the call as text —
    # `send_message(recipient="Mum", text="hi")` — instead of using the
    # structured tool_calls field. Recover those so the tool actually runs.
    if not calls and text.strip():
        parsed = _parse_text_calls(text, tools_schema)
        if parsed:
            calls = parsed
            text = ""   # it wasn't a spoken reply, it was a (mis-formatted) call

    # Now that we KNOW it's real prose (not a mis-typed call), emit it — as one
    # event, so _converse speaks it. Buffering costs a little streaming smoothness
    # on the local path, but never speaks tool-call syntax aloud.
    if text.strip():
        yield {"type": "text", "text": text}

    blocks: List[_Block] = []
    if text.strip():
        blocks.append(_Block(type="text", text=text))
    for c in calls:
        blocks.append(_Block(type="tool_use", id=f"call_{uuid.uuid4().hex[:12]}",
                             name=c["name"], input=c["input"]))
    stop = "tool_use" if calls else "end_turn"
    yield {"type": "final", "message": _Msg(blocks, stop)}


def _parse_text_calls(text: str, tools_schema: List[Dict]) -> List[Dict]:
    """Recover tool calls a weak model emitted as literal text."""
    import ast
    import re

    names = {t["name"] for t in tools_schema}
    if not names:
        return []
    out: List[Dict] = []
    # match  toolname( ...balanced-ish... )  and also ```json {...}``` blobs
    for m in re.finditer(r"\b([a-z_]+)\s*\((.*?)\)", text, re.S):
        name = m.group(1)
        if name not in names:
            continue
        raw = m.group(2).strip()
        args: Dict[str, Any] = {}
        try:
            # kwargs form: key="v", key2='v2', key3=5
            if "=" in raw and not raw.lstrip().startswith("{"):
                for km in re.finditer(r"(\w+)\s*=\s*("
                                      r'"[^"]*"|\'[^\']*\'|[^,]+)', raw):
                    v = km.group(2).strip()
                    try:
                        v = ast.literal_eval(v)
                    except Exception:
                        v = v.strip('"\'')
                    args[km.group(1)] = v
            elif raw.startswith("{"):
                args = ast.literal_eval(raw)
        except Exception:
            args = {}
        out.append({"name": name, "input": args})
    return out


_OLLAMA_BIN = "/Applications/Ollama.app/Contents/Resources/ollama"


def ensure_server() -> None:
    """Start `ollama serve` if it isn't already running (engine startup)."""
    import os
    import subprocess
    import time

    try:
        requests.get(f"{config.OLLAMA_URL}/api/version", timeout=1)
        return   # already up
    except Exception:
        pass
    if not os.path.exists(_OLLAMA_BIN):
        return
    try:
        subprocess.Popen([_OLLAMA_BIN, "serve"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)
        print("[brain] started local Ollama server", flush=True)
    except Exception as exc:
        print(f"[brain] couldn't start Ollama: {exc}", flush=True)


def warm(system: str = "", tools_schema=None) -> None:
    """Load the model into RAM and prime the tool-prefix cache, so the first
    real command is a few seconds, not ~50s. Called at startup off-thread."""
    if not available():
        return
    try:
        payload = {
            "model": config.LOCAL_MODEL,
            "messages": [{"role": "system", "content": system or "You are Jarvis."},
                         {"role": "user", "content": "hello"}],
            "stream": False,
            "keep_alive": config.LOCAL_KEEPALIVE,
            "options": {"num_predict": 1},
        }
        if tools_schema:
            payload["tools"] = _to_ollama_tools(tools_schema)
        requests.post(f"{config.OLLAMA_URL}/api/chat", json=payload, timeout=120)
        print("[brain] local model warmed", flush=True)
    except Exception as exc:
        print(f"[brain] warm failed: {exc}", flush=True)


def available() -> bool:
    """True if the Ollama server is up AND the model is fully downloaded."""
    try:
        tags = requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=2).json()
        names = [m.get("name", "") for m in tags.get("models", [])]
        want = config.LOCAL_MODEL
        return any(n == want or n.split(":")[0] == want.split(":")[0] for n in names)
    except Exception:
        return False
