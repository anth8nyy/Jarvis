"""Send messages via the macOS Messages app to REAL contacts.

Looks the recipient up in Contacts.app to get their actual phone/email, then
sends via iMessage — so "text Mum" goes to your real Mum, not a new junk
conversation with the literal word "mum".
"""

from __future__ import annotations

import difflib
import os
import subprocess
import threading
import time
import unicodedata

from jarvis.registry import Registry, Tool

# --- fuzzy contact resolution -------------------------------------------------
# STT never spells names reliably ("Alix" for Alex, "Mimis" for Μήμης), so we
# fetch the real Contacts list once and fuzzy-match against it instead of
# passing the raw transcription to AppleScript.

_GREEK_LATIN = str.maketrans({
    "α": "a", "β": "v", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "i",
    "θ": "th", "ι": "i", "κ": "k", "λ": "l", "μ": "m", "ν": "n", "ξ": "x",
    "ο": "o", "π": "p", "ρ": "r", "σ": "s", "ς": "s", "τ": "t", "υ": "i",
    "φ": "f", "χ": "h", "ψ": "ps", "ω": "o",
})


def _norm(s: str) -> str:
    """Lowercase, accent-stripped, Greek transliterated — comparison form."""
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.translate(_GREEK_LATIN).strip()


_dir_cache: dict = {"at": 0.0, "people": []}
_dir_lock = threading.Lock()


def _contacts_directory(block: bool = True) -> list:
    """[{name, handles:[phone/email,...]}] for every contact.

    Fetching from Contacts.app takes ~5s, so we serve-stale-while-revalidate:
    if we have ANY cached list we return it INSTANTLY and refresh in the
    background, so a "text Mum" never waits on the fetch. Only the very first
    call (empty cache) blocks — and startup pre-warms that.
    """
    fresh = time.time() - _dir_cache["at"] < 1800   # 30-min freshness
    have = bool(_dir_cache["people"])
    if have and fresh:
        return _dir_cache["people"]
    if have and not block:
        return _dir_cache["people"]
    if have:
        # Stale but usable: refresh in the background, return the old list now.
        threading.Thread(target=lambda: _fetch_contacts(), daemon=True).start()
        return _dir_cache["people"]
    return _fetch_contacts()


def _fetch_from_db() -> list:
    """Read contacts straight from the AddressBook SQLite store — so the
    Contacts APP never has to open. Phones first, then emails, grouped by
    person. Needs Full Disk Access (the engine has it)."""
    import glob
    import sqlite3

    base = os.path.expanduser("~/Library/Application Support/AddressBook")
    dbs = glob.glob(f"{base}/Sources/*/AddressBook-v22.abcddb") + \
        glob.glob(f"{base}/AddressBook-v22.abcddb")
    people_by_pk: dict = {}
    for db in dbs:
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3)
        except Exception:
            continue
        try:
            # name per record
            names = {}
            for pk, fn, ln, org in con.execute(
                "SELECT Z_PK, ZFIRSTNAME, ZLASTNAME, ZORGANIZATION FROM ZABCDRECORD"
            ):
                nm = " ".join(x for x in (fn, ln) if x) or (org or "")
                if nm.strip():
                    names[pk] = nm.strip()
            handles: dict = {}
            for owner, num in con.execute(
                "SELECT ZOWNER, ZFULLNUMBER FROM ZABCDPHONENUMBER WHERE ZFULLNUMBER IS NOT NULL"
            ):
                handles.setdefault(owner, []).append("".join(num.split()))
            for owner, addr in con.execute(
                "SELECT ZOWNER, ZADDRESS FROM ZABCDEMAILADDRESS WHERE ZADDRESS IS NOT NULL"
            ):
                handles.setdefault(owner, []).append(addr.strip())
            for pk, nm in names.items():
                if pk in handles:
                    key = (nm, db)
                    people_by_pk[key] = {"name": nm, "handles": handles[pk]}
        except Exception:
            pass
        finally:
            con.close()
    return list(people_by_pk.values())


def _fetch_contacts() -> list:
    # Preferred: read the database directly — Contacts.app stays closed.
    people = []
    try:
        people = _fetch_from_db()
    except Exception:
        people = []
    # Fallback (e.g. no Full Disk Access): the old AppleScript path, which does
    # need the app briefly. Only used if the DB read found nothing.
    if not people:
        script = '''
        set out to ""
        tell application "Contacts"
            repeat with p in people
                set hs to ""
                repeat with ph in (phones of p)
                    set hs to hs & (value of ph) & ";"
                end repeat
                repeat with em in (emails of p)
                    set hs to hs & (value of em) & ";"
                end repeat
                set out to out & (name of p) & "|" & hs & linefeed
            end repeat
        end tell
        return out
        '''
        if subprocess.run(["pgrep", "-x", "Contacts"], capture_output=True).returncode != 0:
            subprocess.run(["open", "-ga", "Contacts"], capture_output=True)
            time.sleep(1.5)
        try:
            r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            return _dir_cache["people"]
        for line in (r.stdout or "").splitlines():
            name, sep, hs = line.partition("|")
            if not sep or not name.strip():
                continue
            hl = [h.replace(" ", "") for h in hs.split(";") if h.strip()]
            people.append({"name": name.strip(), "handles": hl})
    if people:
        with _dir_lock:
            _dir_cache.update(at=time.time(), people=people)
    return people


def _resolve_contact(spoken: str):
    """Best (name, handle) for what the user said, or None. Fuzzy: 'Alix'
    finds Alex, 'Mimis' finds Μήμης. Prefers phone over email."""
    want = _norm(spoken)
    if not want:
        return None
    best, best_score = None, 0.0
    for p in _contacts_directory():
        if not p["handles"]:
            continue
        name = _norm(p["name"])
        tokens = name.split()
        score = difflib.SequenceMatcher(None, want, name).ratio()
        for tok in tokens:
            score = max(score, difflib.SequenceMatcher(None, want, tok).ratio())
        if want and (want in name or name.startswith(want)):
            score = max(score, 0.95)
        if score > best_score:
            best, best_score = p, score
    if best is None or best_score < 0.6:
        return None
    return best["name"], best["handles"][0]


def _handle_to_name(handle: str) -> str:
    """Reverse lookup: +30695... → 'Alex'. Falls back to the raw handle."""
    h = handle.replace(" ", "")
    tail = h[-8:] if h[-8:].isdigit() or h.startswith("+") else h
    for p in _contacts_directory():
        for ph in p["handles"]:
            if ph == h or (len(ph) >= 8 and ph[-8:] == h[-8:]):
                return p["name"]
    return handle


def _send_to_handle(handle: str, text: str) -> str:
    safe_text = text.replace("\\", "\\\\").replace('"', '\\"')
    safe_handle = handle.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
    tell application "Messages"
        set svc to 1st service whose service type = iMessage
        send "{safe_text}" to buddy "{safe_handle}" of svc
    end tell
    return "OK"
    '''
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if (result.stdout or "").strip() == "OK":
        return "OK"
    return (result.stderr or "").strip() or "the message didn't go through"


def send_message(recipient: str, text: str) -> str:
    # A spoken phone number goes straight through; anything else is resolved
    # against real Contacts with fuzzy matching (so "Alix" reaches Alex).
    digits = recipient.replace(" ", "").replace("-", "")
    if digits.lstrip("+").isdigit() and len(digits) >= 7:
        r = _send_to_handle(digits, text)
        return "Sent, sir." if r == "OK" else f"It didn't go through, sir: {r}"

    resolved = _resolve_contact(recipient)
    if resolved is None:
        return f"I couldn't find anyone like '{recipient}' in your contacts, sir."
    name, handle = resolved
    r = _send_to_handle(handle, text)
    if r == "OK":
        return f"Sent to {name}, sir."
    return f"I found {name} but the message didn't go through, sir: {r}"


def read_messages(contact: str | None = None, count: int = 5) -> str:
    """Read recent messages aloud-ready — optionally just one conversation."""
    import sqlite3

    from jarvis import msgwatch

    handle = None
    who = ""
    if contact:
        resolved = _resolve_contact(contact)
        if resolved is None:
            return f"I couldn't find anyone like '{contact}' in your contacts, sir."
        who, handle = resolved
    try:
        rows = msgwatch.read_recent(count=max(1, min(count, 15)), handle_like=handle)
    except sqlite3.Error:   # "authorization denied" arrives as DatabaseError
        return (
            "I can't see your messages yet, sir — grant me Full Disk Access in "
            "System Settings, Privacy and Security, then restart me."
        )
    if not rows:
        return (f"No recent messages with {who}, sir." if who
                else "No recent messages, sir.")
    lines = []
    for h, body, from_me in rows:
        sender = "You" if from_me else (who or _handle_to_name(h))
        lines.append(f"{sender} said: {body}")
    return " … ".join(lines)


def _accessibility_ok() -> bool:
    """True if we're allowed to drive other apps' UI (right-click menus etc).

    macOS gates this behind Accessibility (assistive access). Without it,
    unsend/delete is impossible — Messages exposes no scripting verb for it.
    """
    probe = (
        'tell application "System Events" to tell process "Finder" '
        "to get name of menu bar 1"
    )
    r = subprocess.run(["osascript", "-e", probe], capture_output=True, text=True)
    return r.returncode == 0


def _open_chat(recipient: str) -> bool:
    """Bring the recipient's conversation to the front (fuzzy name lookup)."""
    resolved = _resolve_contact(recipient)
    if resolved is None:
        return False
    _, handle = resolved
    r = subprocess.run(["open", f"imessage://{handle}"], capture_output=True, text=True)
    return r.returncode == 0


def delete_message(recipient: str | None = None, unsend: bool = True) -> str:
    """Unsend or delete the LAST message you sent — best effort, honest.

    macOS 26 offers NO way to reliably do this automatically: there's no
    scripting API, and the Messages transcript doesn't expose message bubbles to
    the accessibility tree, so a specific bubble can't be safely clicked. This
    tries the automated right-click path where it exists (older layouts), and
    otherwise opens the conversation and tells the user the one manual step —
    rather than pretending it deleted something it couldn't.
    """
    # Focus the right conversation first — useful whether or not automation works.
    opened = _open_chat(recipient) if recipient else False
    who = f" with {recipient}" if recipient else ""

    if not _accessibility_ok():
        base = (
            "macOS won't let me unsend a message without Accessibility access, "
            "sir — turn on Jarvis under System Settings, Privacy & Security, "
            "Accessibility."
        )
        if opened:
            return "I've opened the conversation" + who + ". " + base
        return base

    verb_items = ['"Undo Send"', '"Delete…"', '"Delete"'] if unsend else ['"Delete…"', '"Delete"']
    want_list = "{" + ", ".join(verb_items) + "}"

    # Best-effort automated attempt. It is SAFE by construction: it only ever
    # clicks a menu item literally named Undo Send / Delete, and presses Esc
    # (changing nothing) if no such item exists — so it can't delete the wrong
    # thing. On macOS 26 the bubbles aren't exposed, so this usually no-ops and
    # we fall through to guiding the user.
    script = f'''
    tell application "Messages" to activate
    delay 0.4
    tell application "System Events"
        tell process "Messages"
            set frontmost to true
            set theSA to missing value
            try
                repeat with el in (entire contents of group 1 of window 1)
                    if (role of el) is "AXScrollArea" then
                        set theSA to el
                        exit repeat
                    end if
                end repeat
            end try
            if theSA is missing value then return "NO_TRANSCRIPT"
            set bubbles to (UI elements of theSA)
            if (count of bubbles) is 0 then return "NO_MESSAGES"
            set clicked to ""
            repeat with bi from (count of bubbles) to 1 by -1
                set target to item bi of bubbles
                try
                    perform action "AXShowMenu" of target
                    delay 0.35
                    repeat with wanted in {want_list}
                        try
                            click menu item wanted of menu 1 of target
                            set clicked to (wanted as string)
                            exit repeat
                        end try
                    end repeat
                    if clicked is not "" then exit repeat
                    key code 53 -- Esc: close this menu, try the next element
                end try
            end repeat
            if clicked is "" then return "NO_MENU_ITEM"
            delay 0.4
            try
                click button "Delete" of sheet 1 of window 1
            end try
            try
                click button "Unsend" of sheet 1 of window 1
            end try
            return "OK:" & clicked
        end tell
    end tell
    '''
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    out = (r.stdout or "").strip()
    if out.startswith("OK:"):
        did = out[3:].strip('"')
        if "Undo" in did:
            return "Done, sir — I've unsent your last message."
        return "Done, sir — I've deleted your last message from the conversation."

    # Automation couldn't do it (the norm on current macOS) — be honest and guide.
    step = (
        "right-click your last message and choose Undo Send within two minutes "
        "to unsend it, or Delete to remove it"
        if unsend else
        "right-click your last message and choose Delete"
    )
    if opened:
        return (
            "I've opened the conversation" + who + ", sir, but macOS won't let me "
            "unsend a message for you — " + step + "."
        )
    if recipient and out:  # tried to open but couldn't find the contact
        return f"I couldn't find {recipient} in your contacts, sir."
    return (
        "macOS won't let me unsend a message automatically, sir — open the "
        "conversation and " + step + "."
    )


def register(registry: Registry) -> None:
    registry.register(
        Tool(
            name="send_message",
            description=(
                "Send a text message via the Mac Messages app to one of the user's "
                "contacts (by name — it looks them up in Contacts), or a phone "
                "number. Use ONLY when the user explicitly asks to text/message "
                "someone — NEVER on your own initiative. When they ask, send it "
                "immediately without asking them to confirm."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "recipient": {"type": "string", "description": "Contact name (e.g. 'Mum'), or a phone number."},
                    "text": {"type": "string", "description": "The message to send."},
                },
                "required": ["recipient", "text"],
            },
            handler=send_message,
            # User asked NOT to be re-confirmed — the spoken command is the go-ahead.
            requires_confirmation=False,
        )
    )
    registry.register(
        Tool(
            name="read_messages",
            description=(
                "Read the user's recent iMessages/texts. Use when they ask what "
                "someone sent them, to read their messages, or what the last "
                "message said. Optionally filter to one contact by name (fuzzy — "
                "close names are fine). Repeat any Greek message text VERBATIM in "
                "Greek; never translate it unless asked."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "contact": {"type": "string", "description": "Contact name to filter by (optional)."},
                    "count": {"type": "integer", "description": "How many recent messages (default 5)."},
                },
                "required": [],
            },
            handler=read_messages,
        )
    )
    registry.register(
        Tool(
            name="delete_message",
            description=(
                "Unsend or delete the LAST message the user sent in Messages. Use "
                "when they say 'delete that message', 'unsend that', 'take back what "
                "I sent to X'. Optionally pass the recipient's name to make sure the "
                "right conversation is targeted. Note: unsend only works on iMessages "
                "within 2 minutes; otherwise it's deleted from the user's view only. "
                "Send immediately without asking for confirmation."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "recipient": {
                        "type": "string",
                        "description": "Whose conversation to act in (e.g. 'Mum'). Optional.",
                    },
                    "unsend": {
                        "type": "boolean",
                        "description": "True (default) tries Undo Send first; False deletes outright.",
                    },
                },
                "required": [],
            },
            handler=delete_message,
            requires_confirmation=False,
        )
    )
