"""Place phone / FaceTime calls, and accept or reject incoming ones.

Phone calls go through your iPhone via Continuity (`tel:`); FaceTime uses the
`facetime:`/`facetime-audio:` schemes. Contacts are resolved with the same
fuzzy matcher used for messages, so "call Alix" reaches Alex.

Accepting/rejecting an incoming call has no official API — it's done by UI-
scripting the call notification, which needs Accessibility permission and is
best-effort. Everything reports honestly and never claims a call it couldn't
place or answer.
"""

from __future__ import annotations

import subprocess

from jarvis.registry import Registry, Tool
from jarvis.tools.messages import _resolve_contact


def _dial(url: str) -> bool:
    return subprocess.run(["open", url], capture_output=True, text=True).returncode == 0


def _target(recipient: str):
    """(name, handle) for a spoken recipient, or (None, digits) for a number."""
    digits = recipient.replace(" ", "").replace("-", "")
    if digits.lstrip("+").isdigit() and len(digits) >= 3:
        return None, digits
    r = _resolve_contact(recipient)
    return (r[0], r[1]) if r else (None, None)


def call_phone(recipient: str) -> str:
    """Ring a contact/number on the iPhone via Continuity."""
    name, handle = _target(recipient)
    if not handle:
        return f"I couldn't find anyone like '{recipient}' to call, sir."
    if not _dial(f"tel://{handle}"):
        return "I couldn't start the call, sir."
    who = name or handle
    return (f"Calling {who}, sir — confirm it on your iPhone if it asks.")


def call_facetime(recipient: str, video: bool = True) -> str:
    """Start a FaceTime call (video by default, or audio-only)."""
    name, handle = _target(recipient)
    if not handle:
        return f"I couldn't find anyone like '{recipient}' to call, sir."
    scheme = "facetime" if video else "facetime-audio"
    if not _dial(f"{scheme}://{handle}"):
        return "I couldn't start FaceTime, sir."
    kind = "FaceTime" if video else "FaceTime audio"
    return f"Starting {kind} with {name or handle}, sir."


def _answer_call(accept: bool) -> str:
    """Click Accept/Decline on the incoming-call notification (UI scripting)."""
    verb = "Accept" if accept else "Decline"
    # The call banner lives in Notification Center's UI; the buttons are named
    # Accept / Decline. Try there; fall back to the FaceTime window's buttons.
    script = f'''
    tell application "System Events"
        set done to false
        try
            tell process "NotificationCenter"
                repeat with w in windows
                    repeat with b in (buttons of w)
                        if (name of b) is "{verb}" or (description of b) is "{verb}" then
                            click b
                            set done to true
                            exit repeat
                        end if
                    end repeat
                    if done then exit repeat
                end repeat
            end tell
        end try
        if not done then
            try
                tell process "FaceTime"
                    click (first button whose name is "{verb}")
                    set done to true
                end tell
            end try
        end if
        return done
    end tell
    '''
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if "not allowed assistive access" in (r.stderr or ""):
        return ("I need Accessibility access to answer calls, sir — enable Jarvis "
                "under System Settings, Privacy and Security, Accessibility.")
    if (r.stdout or "").strip() == "true":
        return "Accepted, sir." if accept else "Declined, sir."
    return "I don't see an incoming call to answer, sir."


def accept_call() -> str:
    return _answer_call(True)


def reject_call() -> str:
    return _answer_call(False)


def end_call() -> str:
    """Hang up the current FaceTime/phone call."""
    r = subprocess.run(
        ["osascript", "-e",
         'tell application "System Events" to tell process "FaceTime" to '
         'click (first button whose name is "End")'],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        return "Call ended, sir."
    return "There's no active call I can end, sir."


def register(registry: Registry) -> None:
    registry.register(Tool(
        name="call_phone",
        description=(
            "Place a PHONE call to a contact (or number) through the user's "
            "iPhone. Use for 'call Mum', 'phone Alex', 'ring 210…'. Needs the "
            "iPhone nearby with Continuity/Wi-Fi calling."
        ),
        input_schema={"type": "object", "properties": {
            "recipient": {"type": "string", "description": "Contact name or phone number."}},
            "required": ["recipient"]},
        handler=call_phone,
        requires_confirmation=True,
    ))
    registry.register(Tool(
        name="call_facetime",
        description=(
            "Start a FaceTime call with a contact/number. Use for 'FaceTime "
            "Alex', 'video call Mum'. Set video=false for FaceTime audio."
        ),
        input_schema={"type": "object", "properties": {
            "recipient": {"type": "string", "description": "Contact name or number."},
            "video": {"type": "boolean", "description": "True for video (default), false for audio."}},
            "required": ["recipient"]},
        handler=call_facetime,
        requires_confirmation=True,
    ))
    registry.register(Tool(
        name="accept_call",
        description="Answer/accept an incoming phone or FaceTime call. Use for 'answer', 'accept the call', 'pick up'.",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=accept_call,
    ))
    registry.register(Tool(
        name="reject_call",
        description="Reject/decline an incoming call. Use for 'reject', 'decline', 'ignore the call', 'hang up on them'.",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=reject_call,
    ))
    registry.register(Tool(
        name="end_call",
        description="End/hang up the call currently in progress.",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=end_call,
    ))
