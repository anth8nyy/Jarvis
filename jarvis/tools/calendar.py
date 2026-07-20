"""Schedule events into the macOS Calendar app via AppleScript.

Local-only tool (like mac.py). Creating an event is a state change on your
machine; macOS will prompt for Automation permission the first time.
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime

from jarvis.registry import Registry, Tool


def _osascript(script: str, timeout: float = 120.0) -> str:
    try:
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("the Calendar app took too long to answer")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "AppleScript failed")
    return result.stdout.strip()


def create_event(title: str, start: str, duration_minutes: int = 60) -> str:
    """Create a calendar event. `start` is ISO 8601 (e.g. 2026-07-15T14:00).

    Builds the AppleScript date from explicit numeric components — a formatted
    date string is parsed with the Mac's locale (e.g. DD/MM vs MM/DD), which
    silently created events on the wrong date.
    """
    from datetime import timedelta

    dt = datetime.fromisoformat(start)
    end = dt + timedelta(minutes=duration_minutes)
    safe_title = title.replace('"', "'")

    def make_date(d) -> str:
        # set day to 1 before month to avoid end-of-month rollover
        return (
            f'(my mkdate({d.year}, {d.month}, {d.day}, {d.hour}, {d.minute}))'
        )

    script = f'''
    on mkdate(y, m, d, hh, mm)
        set theDate to current date
        set year of theDate to y
        set day of theDate to 1
        set month of theDate to m
        set day of theDate to d
        set hours of theDate to hh
        set minutes of theDate to mm
        set seconds of theDate to 0
        return theDate
    end mkdate
    tell application "Calendar"
        tell calendar 1
            make new event with properties {{summary:"{safe_title}", start date:{make_date(dt)}, end date:{make_date(end)}}}
        end tell
    end tell
    '''
    _osascript(script)
    return f'Scheduled "{title}" for {dt.strftime("%A %B %-d at %-I:%M %p")}.'


def _mkdate_handler() -> str:
    return (
        "on mkdate(y, m, d, hh, mm)\n"
        "  set theDate to current date\n"
        "  set year of theDate to y\n  set day of theDate to 1\n"
        "  set month of theDate to m\n  set day of theDate to d\n"
        "  set hours of theDate to hh\n  set minutes of theDate to mm\n"
        "  set seconds of theDate to 0\n  return theDate\nend mkdate\n"
    )


_SECONDS = re.compile(r"(\d{1,2}:\d{2}):\d{2}")


def _collect_events(d0_expr: str, d1_expr: str, with_date: bool = False) -> list:
    """Events starting in [d0, d1) across every calendar, formatted for speech.

    Two AppleScript landmines are worked around here:
    * `set evs to (every event ...)` materialises a LIST of references — reading
      `summary of` a list throws -1728. `a reference to` keeps it an object
      specifier, so properties bulk-fetch in one shot (and can't misalign,
      since every list comes from the same specifier).
    * Looping `repeat with ev in (every event ...)` yields unresolved refs and
      throws -1700 on `start date of ev`. Bulk access resolves them properly.
    """
    script = f'''
    {_mkdate_handler()}
    set d0 to {d0_expr}
    set d1 to {d1_expr}
    set out to ""
    tell application "Calendar"
        repeat with ci from 1 to (count of calendars)
            tell calendar ci
                set evs to a reference to (every event whose start date ≥ d0 and start date < d1)
                set n to (count of evs)
                if n > 0 then
                    set sums to summary of evs
                    set sts to start date of evs
                    set ads to allday event of evs
                    repeat with i from 1 to n
                        set out to out & (item i of sums) & "~" & ¬
                            (short date string of (item i of sts)) & "~" & ¬
                            (time string of (item i of sts)) & "~" & ¬
                            (item i of ads) & "||"
                    end repeat
                end if
            end tell
        end repeat
    end tell
    return out
    '''
    raw = _osascript(script)
    out = []
    for chunk in raw.split("||"):
        # rsplit, not split: the last 3 fields are ours, but a title is free to
        # contain "~" and must not be silently dropped.
        parts = chunk.strip().rsplit("~", 3)
        if len(parts) != 4:
            continue
        title, date_s, time_s, allday = (p.strip() for p in parts)
        if not title:
            continue
        # All-day events have a meaningless 12:00:00 AM start — don't read it out.
        is_allday = allday.lower() == "true"
        time_s = _SECONDS.sub(r"\1", time_s)   # 7:00:00 PM → 7:00 PM
        if is_allday:
            when = f"all day on {date_s}" if with_date else "all day"
        elif with_date:
            when = f"{date_s} at {time_s}"
        else:
            when = f"at {time_s}"
        out.append(f"{title} {when}")
    return out


def list_today_events() -> str:
    """What's on the calendar today, across all calendars."""
    from datetime import date, timedelta

    t = date.today()
    tm = t + timedelta(days=1)
    items = _collect_events(
        f"mkdate({t.year}, {t.month}, {t.day}, 0, 0)",
        f"mkdate({tm.year}, {tm.month}, {tm.day}, 0, 0)",
    )
    if not items:
        return "Nothing on your calendar today, sir."
    return "Today you have: " + "; ".join(items) + "."


def list_events(start_date: str, end_date: str | None = None, limit: int = 25) -> str:
    """Everything scheduled between two dates (ISO YYYY-MM-DD), all calendars.

    Dates are built from numeric components via mkdate for the same reason
    create_event does: a formatted date string gets parsed in the Mac's locale
    (DD/MM vs MM/DD) and silently reads the wrong window.
    """
    from datetime import date, timedelta

    d0 = date.fromisoformat(start_date)
    d1 = date.fromisoformat(end_date) if end_date else d0 + timedelta(days=7)
    if d1 <= d0:
        d1 = d0 + timedelta(days=1)
    # Calendar's AppleScript walks events one by one; a huge window can hang for
    # minutes. Cap it and say so rather than appearing to freeze.
    span = (d1 - d0).days
    capped = span > 180
    if capped:
        d1 = d0 + timedelta(days=180)

    items = _collect_events(
        f"mkdate({d0.year}, {d0.month}, {d0.day}, 0, 0)",
        f"mkdate({d1.year}, {d1.month}, {d1.day}, 0, 0)",
        with_date=True,
    )
    if not items:
        return f"Nothing scheduled between {d0.isoformat()} and {d1.isoformat()}, sir."

    total = len(items)
    lines = "; ".join(items[:limit])
    msg = f"You have {total} event(s): {lines}"
    if total > limit:
        msg += f" … and {total - limit} more"
    if capped:
        msg += " (I only looked 6 months ahead — ask for a specific range for more)"
    return msg + "."


def delete_calendar_event(title: str) -> str:
    """Delete calendar events whose title matches."""
    safe = title.replace('"', "'")
    # Guard on count first: `delete evs` where evs is an empty {} throws -1700
    # ("can't make {} into type specifier"), and with several calendars most of
    # them won't match — so an unguarded delete failed essentially every time.
    script = f'''
    set n to 0
    tell application "Calendar"
        repeat with ci from 1 to (count of calendars)
            tell calendar ci
                set evs to a reference to (every event whose summary contains "{safe}")
                set c to (count of evs)
                if c > 0 then
                    set n to n + c
                    delete evs
                end if
            end tell
        end repeat
    end tell
    return n as string
    '''
    n = _osascript(script)
    if n and n != "0":
        return f"Deleted {n} event(s) matching '{title}', sir."
    return f"I couldn't find an event called '{title}', sir."


def reschedule_calendar_event(title: str, new_start: str, duration_minutes: int = 60) -> str:
    """Move the first event matching `title` to a new ISO 8601 start time."""
    from datetime import timedelta

    dt = datetime.fromisoformat(new_start)
    end = dt + timedelta(minutes=duration_minutes)
    safe = title.replace('"', "'")
    # Build the dates BEFORE entering the Calendar tell block: inside it, a bare
    # mkdate(...) is sent to Calendar rather than this script and dies with
    # -1708 "Can't continue mkdate". (create_event dodges this with `my`.)
    script = f'''
    {_mkdate_handler()}
    set newStart to mkdate({dt.year},{dt.month},{dt.day},{dt.hour},{dt.minute})
    set newEnd to mkdate({end.year},{end.month},{end.day},{end.hour},{end.minute})
    tell application "Calendar"
        repeat with ci from 1 to (count of calendars)
            tell calendar ci
                set evs to a reference to (every event whose summary contains "{safe}")
                if (count of evs) > 0 then
                    set ev to first event whose summary contains "{safe}"
                    -- Order matters: Calendar rejects any write that leaves
                    -- start ≥ end, so move the far edge out of the way first.
                    if newStart ≥ (start date of ev) then
                        set end date of ev to newEnd
                        set start date of ev to newStart
                    else
                        set start date of ev to newStart
                        set end date of ev to newEnd
                    end if
                    return "ok"
                end if
            end tell
        end repeat
    end tell
    return "none"
    '''
    r = _osascript(script)
    if r == "ok":
        return f"Moved '{title}' to {dt.strftime('%A %B %-d at %-I:%M %p')}, sir."
    return f"I couldn't find an event called '{title}', sir."


def register(registry: Registry) -> None:
    registry.register(
        Tool(
            name="create_calendar_event",
            description=(
                "Add an event or reminder to the user's macOS Calendar app. Use "
                "whenever they want something on their calendar or a reminder at a "
                "specific date/time ('schedule X', 'put X on my calendar', 'remind "
                "me to X tomorrow at 3pm', 'add a meeting Friday at noon'). Provide a "
                "title and an ISO 8601 start time — call get_datetime first to turn "
                "relative times like 'tomorrow at 3pm' into an exact timestamp. "
                "Optional duration in minutes (default 60). (For a short countdown "
                "like 'in 5 minutes', use remind_me instead.) Use ONLY when the "
                "user explicitly asks — never on your own initiative. They are "
                "asked to confirm before it's created, so don't ask them yourself."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "The event title."},
                    "start": {"type": "string", "description": "ISO 8601 start, e.g. 2026-07-15T15:00."},
                    "duration_minutes": {"type": "integer", "description": "Length in minutes (default 60)."},
                },
                "required": ["title", "start"],
            },
            handler=create_event,
            # User asked to be asked "are you sure?" before anything is added.
            requires_confirmation=True,
        )
    )
    registry.register(
        Tool(
            name="list_today_events",
            description="List everything on the user's calendar for today. Use when they ask what they have planned today / what's on their schedule.",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=list_today_events,
        )
    )
    registry.register(
        Tool(
            name="list_events",
            description=(
                "List calendar events over ANY date range, across every calendar "
                "(Home, Work, Birthdays, holidays…). Use for anything beyond today: "
                "'what's on this week', 'am I free Friday', 'what's coming up', "
                "'what's in my calendar next month'. Dates are ISO YYYY-MM-DD — call "
                "get_datetime first to resolve relative ones like 'next week'. "
                "end_date defaults to a week after start_date. For today only, "
                "list_today_events is faster."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "First day to include, ISO YYYY-MM-DD."},
                    "end_date": {"type": "string", "description": "Exclusive last day, ISO YYYY-MM-DD. Defaults to start_date + 7 days."},
                },
                "required": ["start_date"],
            },
            handler=list_events,
        )
    )
    registry.register(
        Tool(
            name="delete_calendar_event",
            description="Delete/cancel a calendar event by (part of) its title. Use when they want to remove or cancel something from the calendar.",
            input_schema={
                "type": "object",
                "properties": {"title": {"type": "string", "description": "Words from the event's title."}},
                "required": ["title"],
            },
            handler=delete_calendar_event,
            # User asked NOT to be re-asked — the voice command is the go-ahead.
            requires_confirmation=False,
        )
    )
    registry.register(
        Tool(
            name="reschedule_calendar_event",
            description="Move/edit a calendar event to a new time. Give the event title and the new ISO 8601 start time (use get_datetime for relative times).",
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Words from the event's title."},
                    "new_start": {"type": "string", "description": "New ISO 8601 start, e.g. 2026-07-16T15:00."},
                    "duration_minutes": {"type": "integer", "description": "Length in minutes (default 60)."},
                },
                "required": ["title", "new_start"],
            },
            handler=reschedule_calendar_event,
        )
    )
