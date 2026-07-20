"""Watch the Messages database and announce new incoming texts.

Messages offers no scripting API for reading, so we poll its sqlite store
(~/Library/Messages/chat.db) read-only. Reading it requires the user to grant
**Full Disk Access** to Jarvis — until then this degrades gracefully: one
honest notice, then silence.

Modern macOS often leaves `message.text` NULL and stores the body in the
`attributedBody` blob (an NSKeyedArchiver typedstream); `_extract_text`
implements the well-known heuristic for pulling the plain string out of it.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import Callable, List, Optional, Tuple

DB = os.path.expanduser("~/Library/Messages/chat.db")

# Apple epochs: chat.db dates are nanoseconds since 2001-01-01.
_APPLE_EPOCH = 978307200

# Only announce a text delivered within this many seconds — never hours-old
# messages you saw long ago. (Also gated on unread in the query.)
RECENT_SECONDS = 180


def _connect() -> sqlite3.Connection:
    # mode=ro so we can never corrupt Messages' database, even in theory.
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=3)


def _extract_text(text: Optional[str], blob: Optional[bytes]) -> str:
    """The message body: plain `text` when present, else parsed from the
    typedstream `attributedBody` (NSString length-prefixed payload)."""
    if text:
        return text.strip()
    if not blob:
        return ""
    try:
        idx = blob.find(b"NSString")
        if idx == -1:
            return ""
        data = blob[idx + 8:]
        plus = data.find(b"+")
        if plus == -1 or plus > 8:   # '+' marker sits right after the class info
            return ""
        data = data[plus + 1:]
        ln = data[0]
        if ln == 0x81:  # long form: next 2 bytes are a little-endian length
            ln = int.from_bytes(data[1:3], "little")
            payload = data[3:3 + ln]
        else:
            payload = data[1:1 + ln]
        return payload.decode("utf-8", "ignore").strip()
    except Exception:
        return ""


def read_recent(count: int = 5, handle_like: Optional[str] = None) -> List[Tuple[str, str, bool]]:
    """Last `count` messages, newest LAST: [(handle, text, is_from_me)].
    Raises sqlite3.OperationalError («authorization denied») without FDA."""
    q = """
        SELECT h.id, m.text, m.attributedBody, m.is_from_me
        FROM message m JOIN handle h ON m.handle_id = h.ROWID
        WHERE (m.text IS NOT NULL OR m.attributedBody IS NOT NULL)
        {extra}
        ORDER BY m.ROWID DESC LIMIT ?
    """
    args: list = []
    extra = ""
    if handle_like:
        extra = "AND replace(h.id, ' ', '') LIKE ?"
        args.append(f"%{handle_like[-8:]}")
    args.append(count)
    with _connect() as con:
        rows = con.execute(q.format(extra=extra), args).fetchall()
    out = []
    for handle, text, blob, from_me in reversed(rows):
        body = _extract_text(text, blob)
        if body:
            out.append((handle, body, bool(from_me)))
    return out


class MessagesWatcher:
    """Polls for new incoming messages and hands them to `on_message`.

    on_message(sender_handle, text) -> bool — return False to have the same
    message retried shortly (e.g. Jarvis was mid-conversation).
    on_denied() fires ONCE if the database can't be read (no Full Disk Access).
    """

    def __init__(
        self,
        on_message: Callable[[str, str], bool],
        on_denied: Callable[[], None] | None = None,
        poll_seconds: float = 3.0,
    ):
        self.on_message = on_message
        self.on_denied = on_denied
        self.poll = poll_seconds
        self._stop = threading.Event()
        self._pending: List[Tuple[str, str, float]] = []

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        # Baseline: only announce messages that arrive AFTER we started.
        try:
            with _connect() as con:
                last = con.execute("SELECT COALESCE(MAX(ROWID),0) FROM message").fetchone()[0]
            print("[msgwatch] watching for new messages", flush=True)
        except sqlite3.Error:   # no FDA → DatabaseError("authorization denied")
            print("[msgwatch] no Full Disk Access — can't see messages", flush=True)
            if self.on_denied:
                try:
                    self.on_denied()
                except Exception:
                    pass
            return

        while not self._stop.is_set():
            time.sleep(self.poll)
            # Only announce messages that are BOTH just-delivered (last few
            # minutes) AND still unread — never last night's, never ones already
            # seen on the phone. chat.db `date` is ns since 2001-01-01.
            recent_ns = int((time.time() - _APPLE_EPOCH - RECENT_SECONDS) * 1_000_000_000)
            try:
                with _connect() as con:
                    rows = con.execute(
                        """
                        SELECT m.ROWID, h.id, m.text, m.attributedBody
                        FROM message m JOIN handle h ON m.handle_id = h.ROWID
                        WHERE m.ROWID > ? AND m.is_from_me = 0
                          AND COALESCE(m.is_read, 0) = 0
                          AND m.date > ?
                        ORDER BY m.ROWID
                        """,
                        (last, recent_ns),
                    ).fetchall()
            except sqlite3.Error:
                continue  # transient lock — try again next tick
            for rowid, handle, text, blob in rows:
                last = max(last, rowid)
                body = _extract_text(text, blob)
                if body:
                    self._pending.append((handle, body, time.time()))
            # Deliver (and retry anything Jarvis was too busy to say).
            still = []
            for handle, body, born in self._pending:
                if time.time() - born > 90:
                    continue  # held too long (Jarvis was busy/muted) — drop it
                ok = False
                try:
                    ok = self.on_message(handle, body)
                except Exception:
                    ok = True  # don't loop forever on a broken announcement
                if not ok:
                    still.append((handle, body, born))
            self._pending = still
