"""Ask your own notes — search Apple Notes and answer from what's in them.

No embedding model, no index to maintain: notes are fetched live over
AppleScript (cached a few minutes), ranked by simple keyword overlap, and the
best few are handed to the brain to answer from. For a personal notes corpus
that beats hauling in a 2GB embedding stack, and it's always fresh.
"""

from __future__ import annotations

import re
import subprocess
import threading
import time

from jarvis.registry import Registry, Tool

_cache: dict = {"at": 0.0, "notes": []}
_lock = threading.Lock()

_TAG = re.compile(r"<[^>]+>")
_STOP = {
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are",
    "was", "what", "did", "do", "i", "my", "me", "about", "with", "at", "it",
}


def _fetch_notes() -> list:
    """[(folder, title, plain-text body)] for every note. Cached 5 min."""
    with _lock:
        if time.time() - _cache["at"] < 300 and _cache["notes"]:
            return _cache["notes"]
    script = '''
    set out to ""
    tell application "Notes"
        repeat with f in folders
            set fn to (name of f)
            if fn is not "Recently Deleted" then
                repeat with n in notes of f
                    set out to out & "<<NOTE>>" & fn & "<<T>>" & (name of n) & "<<B>>" & (body of n)
                end repeat
            end if
        end repeat
    end tell
    return out
    '''
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=60)
    notes = []
    for chunk in (r.stdout or "").split("<<NOTE>>"):
        if "<<T>>" not in chunk:
            continue
        folder, rest = chunk.split("<<T>>", 1)
        title, _, body = rest.partition("<<B>>")
        text = _TAG.sub(" ", body).replace("&nbsp;", " ")
        text = re.sub(r"\s+", " ", text).strip()
        notes.append((folder.strip(), title.strip(), text[:4000]))
    if notes:
        with _lock:
            _cache.update(at=time.time(), notes=notes)
    return notes


def _score(question: str, note: tuple) -> float:
    words = {w for w in re.findall(r"\w+", question.lower()) if w not in _STOP and len(w) > 2}
    if not words:
        return 0.0
    # Folder counts too — "my business notes" should hit the Business folder.
    hay = f"{note[0]} {note[1]} {note[2]}".lower()
    strong = f"{note[0]} {note[1]}".lower()
    s = sum(2.0 if w in strong else 1.0 for w in words if w in hay)
    return s / len(words)


def ask_notes(question: str) -> str:
    """The most relevant notes for the question, for the brain to answer from."""
    notes = _fetch_notes()
    if not notes:
        return "There are no notes to search, sir — or Notes isn't reachable."
    ranked = sorted(notes, key=lambda n: _score(question, n), reverse=True)
    best = [n for n in ranked[:3] if _score(question, n) > 0]
    if not best:
        return f"Nothing in your notes mentions that ({len(notes)} notes searched)."
    out = []
    for folder, title, text in best:
        out.append(f"[{folder} / {title}] {text[:1200]}")
    return ("Relevant notes found — answer the user's question from these, and "
            "say which note it came from:\n" + "\n---\n".join(out))


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "'")


def _html_body(title: str, text: str) -> str:
    lines = "".join(f"<div>{_esc(ln)}</div>" for ln in text.split("\n"))
    return f"<div><b>{_esc(title)}</b></div>{lines}"


def create_note(title: str, content: str = "", folder: str = "Notes") -> str:
    """Create a new Apple Note."""
    body = _html_body(title, content)
    safe_folder = folder.replace('"', "'")
    script = f'''
    tell application "Notes"
        try
            set f to folder "{safe_folder}"
        on error
            set f to folder "Notes"
        end try
        make new note at f with properties {{body:"{body.replace('"', '&quot;')}"}}
    end tell
    return "OK"
    '''
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=20)
    _cache["at"] = 0   # invalidate so reads see the new note
    if (r.stdout or "").strip() == "OK":
        return f"Done, sir — I've created a note titled '{title}'."
    return "I couldn't create that note, sir."


def _find_note_title(query: str):
    """Best-matching existing note title, or None."""
    import difflib
    notes = _fetch_notes()
    titles = [n[1] for n in notes]
    q = query.lower()
    exact = [t for t in titles if q in t.lower()]
    if exact:
        return min(exact, key=len)
    close = difflib.get_close_matches(query, titles, n=1, cutoff=0.5)
    return close[0] if close else None


def append_note(note: str, text: str) -> str:
    """Add a line to an existing note (matched by title)."""
    title = _find_note_title(note)
    if not title:
        return f"I couldn't find a note called '{note}', sir."
    safe = title.replace('"', "'")
    add = "".join(f"<div>{_esc(ln)}</div>" for ln in text.split("\n"))
    script = f'''
    tell application "Notes"
        set n to first note whose name is "{safe}"
        set body of n to (body of n) & "{add.replace('"', '&quot;')}"
    end tell
    return "OK"
    '''
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=20)
    _cache["at"] = 0
    if (r.stdout or "").strip() == "OK":
        return f"Added to '{title}', sir."
    return f"I couldn't edit '{title}', sir."


def delete_note(note: str) -> str:
    """Delete a note (matched by title). Gated by confirmation."""
    title = _find_note_title(note)
    if not title:
        return f"I couldn't find a note called '{note}', sir."
    safe = title.replace('"', "'")
    script = f'''
    tell application "Notes"
        delete (first note whose name is "{safe}")
    end tell
    return "OK"
    '''
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=20)
    _cache["at"] = 0
    if (r.stdout or "").strip() == "OK":
        return f"Deleted the note '{title}', sir."
    return f"I couldn't delete '{title}', sir."


def register(registry: Registry) -> None:
    registry.register(Tool(
        name="create_note",
        description=("Create a new note in Apple Notes. Use for 'make a note', "
                     "'write a note', 'note down…', 'start a new note about X'."),
        input_schema={"type": "object", "properties": {
            "title": {"type": "string", "description": "The note's title."},
            "content": {"type": "string", "description": "The note's body text (optional)."},
            "folder": {"type": "string", "description": "Folder name, e.g. 'Business' (optional)."}},
            "required": ["title"]},
        handler=create_note,
    ))
    registry.register(Tool(
        name="append_note",
        description=("Add text to an existing note (found by title). Use for 'add "
                     "X to my note', 'write X in my Y note'."),
        input_schema={"type": "object", "properties": {
            "note": {"type": "string", "description": "The note's title (fuzzy match)."},
            "text": {"type": "string", "description": "Text to add."}},
            "required": ["note", "text"]},
        handler=append_note,
    ))
    registry.register(Tool(
        name="delete_note",
        description="Delete a note by its title. Use for 'delete my X note', 'remove the note about Y'.",
        input_schema={"type": "object", "properties": {
            "note": {"type": "string", "description": "The note's title (fuzzy match)."}},
            "required": ["note"]},
        handler=delete_note,
        requires_confirmation=True,
    ))
    registry.register(Tool(
        name="ask_notes",
        description=(
            "Search the user's own Apple Notes and answer from their content. "
            "Use when they ask about something they wrote down or decided: "
            "'what did I note about X', 'what's in my business notes', 'what did "
            "I decide about the trip'. Answer from the returned notes and cite "
            "which note."
        ),
        input_schema={"type": "object", "properties": {
            "question": {"type": "string", "description": "What the user wants to know."}},
            "required": ["question"]},
        handler=ask_notes,
    ))
