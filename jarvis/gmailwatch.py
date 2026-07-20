"""Watch one or more Gmail accounts and announce new REAL email.

Connects over IMAP with an app password per account (no Google Cloud project
needed). Uses Gmail's own `category:primary` filter, so Promotions, Social,
Updates, Forums and Spam are excluded automatically — you only hear about mail
that actually matters, told with which account it landed on and who it's from.

Setup (once per account, done by the user):
  1. Google Account → Security → turn on 2-Step Verification.
  2. Security → App passwords → generate one (pick "Mail" / "Mac").
  3. Add it to data/gmail_accounts.json (see gmail_accounts.example.json).
"""

from __future__ import annotations

import email
import imaplib
import json
import os
import threading
import time
from email.header import decode_header
from email.utils import parseaddr
from typing import Callable, List

_DATA = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
_CONFIG = os.path.join(_DATA, "gmail_accounts.json")


def accounts() -> List[dict]:
    """[{name,email,app_password}] from data/gmail_accounts.json, or [].

    Skips entries whose password is still a placeholder, so an un-filled file
    doesn't cause failed logins."""
    try:
        with open(_CONFIG) as fh:
            # strict=False tolerates stray newlines pasted inside a value, which
            # otherwise make the whole file unparseable.
            data = json.loads(fh.read(), strict=False)
    except Exception:
        return []
    out = []
    for a in data:
        pw = "".join((a.get("app_password") or "").split())   # drop ALL whitespace
        if a.get("email") and pw and "PASTE" not in pw.upper() and "REPLACE" not in pw.upper():
            out.append({"name": a.get("name") or "Gmail", "email": a["email"], "app_password": pw})
    return out


def _decode(s: str) -> str:
    if not s:
        return ""
    out = []
    for part, enc in decode_header(s):
        if isinstance(part, bytes):
            try:
                out.append(part.decode(enc or "utf-8", "ignore"))
            except Exception:
                out.append(part.decode("utf-8", "ignore"))
        else:
            out.append(part)
    return "".join(out).strip()


def _sender_name(from_header: str) -> str:
    name, addr = parseaddr(from_header)
    name = _decode(name)
    return name or addr or "someone"


def _connect(acc: dict):
    m = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    m.login(acc["email"], acc["app_password"].replace(" ", ""))
    m.select("INBOX", readonly=True)
    return m


def _primary_unread_uids(m) -> List[bytes]:
    # X-GM-RAW = Gmail's own search syntax. category:primary excludes
    # promotions/social/updates/forums; is:unread limits to new mail.
    typ, data = m.uid("search", None, "X-GM-RAW", '"is:unread category:primary"')
    if typ != "OK" or not data or not data[0]:
        return []
    return data[0].split()


def read_recent(limit: int = 5) -> str:
    """Aloud-ready summary of recent unread primary email across all accounts."""
    accs = accounts()
    if not accs:
        return ("I don't have access to your Gmail yet, sir — add an app password "
                "to data/gmail_accounts.json and I'll watch it.")
    lines: List[str] = []
    for acc in accs:
        try:
            m = _connect(acc)
            uids = _primary_unread_uids(m)[-limit:]
            for uid in reversed(uids):
                typ, msg_data = m.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
                if typ != "OK" or not msg_data or not msg_data[0]:
                    continue
                hdr = email.message_from_bytes(msg_data[0][1])
                who = _sender_name(hdr.get("From", ""))
                subj = _decode(hdr.get("Subject", "")) or "no subject"
                lines.append(f"On {acc['name']}, from {who}: {subj}")
            m.logout()
        except Exception:
            lines.append(f"I couldn't reach your {acc.get('name','Gmail')} account, sir.")
    if not lines:
        return "No new email in your primary inboxes, sir."
    return " … ".join(lines[:limit])


class GmailWatcher:
    """Polls each account and calls on_email(account_name, sender, subject) for
    each NEW primary-inbox message that arrives after startup."""

    def __init__(self, on_email: Callable[[str, str, str], bool], poll_seconds: float = 60.0):
        self.on_email = on_email
        self.poll = poll_seconds
        self._stop = threading.Event()
        self._seen: dict = {}   # account email -> set of uids already known

    def start(self) -> None:
        if accounts():
            threading.Thread(target=self._run, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        # Baseline: everything currently unread is "already seen" — only NEW
        # mail from here on is announced.
        for acc in accounts():
            try:
                m = _connect(acc)
                self._seen[acc["email"]] = set(_primary_unread_uids(m))
                m.logout()
            except Exception:
                self._seen[acc["email"]] = set()
        print(f"[gmail] watching {len(accounts())} account(s)", flush=True)

        while not self._stop.is_set():
            time.sleep(self.poll)
            for acc in accounts():
                try:
                    m = _connect(acc)
                    uids = _primary_unread_uids(m)
                    seen = self._seen.setdefault(acc["email"], set())
                    for uid in uids:
                        if uid in seen:
                            continue
                        seen.add(uid)
                        typ, d = m.uid("fetch", uid, "(INTERNALDATE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
                        if typ != "OK" or not d or not d[0]:
                            continue
                        # Only announce mail that actually ARRIVED just now — not
                        # an old unread that surfaced for some other reason.
                        try:
                            idate = imaplib.Internaldate2tuple(d[0][0])
                            if idate and (time.time() - time.mktime(idate)) > 900:
                                continue   # older than 15 min → skip
                        except Exception:
                            pass
                        hdr = email.message_from_bytes(d[0][1])
                        who = _sender_name(hdr.get("From", ""))
                        subj = _decode(hdr.get("Subject", "")) or "no subject"
                        try:
                            self.on_email(acc["name"], who, subj)
                        except Exception:
                            pass
                    m.logout()
                except Exception:
                    continue
