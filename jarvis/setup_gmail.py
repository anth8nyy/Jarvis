"""Interactive Gmail setup — you enter your accounts, this writes the config.

Run it with:   ./.venv/bin/python -m jarvis.setup_gmail

For each account it asks for a friendly name, the email, and the 16-character
app password (typed into THIS prompt — it goes straight into the local config
file on your Mac). It then tests the login so you know it works immediately.
"""

from __future__ import annotations

import getpass
import json
import os

from jarvis import gmailwatch

_CONFIG = gmailwatch._CONFIG


def _test(email: str, app_password: str) -> str:
    import imaplib
    try:
        m = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        m.login(email, app_password.replace(" ", ""))
        m.select("INBOX", readonly=True)
        m.logout()
        return "OK"
    except Exception as exc:
        return f"FAILED: {exc}"


def main() -> None:
    print("\n— Jarvis Gmail setup —")
    print("For each Gmail account: a name Jarvis will say, the address, and the")
    print("app password (Google Account → Security → App passwords).")
    print("Leave the name blank and press Enter when you're done.\n")

    accounts = []
    while True:
        name = input("Account name (e.g. Personal), or Enter to finish: ").strip()
        if not name:
            break
        email = input("  Gmail address: ").strip()
        pw = getpass.getpass("  App password (hidden as you type/paste): ").strip()
        if not email or not pw:
            print("  Skipped — need both an address and a password.\n")
            continue
        print("  Testing…", end=" ", flush=True)
        result = _test(email, pw)
        print(result)
        if result != "OK":
            keep = input("  Login failed. Save it anyway? (y/N): ").strip().lower()
            if keep != "y":
                print("  Not saved.\n")
                continue
        accounts.append({"name": name, "email": email, "app_password": pw})
        print(f"  Saved {name}.\n")

    if not accounts:
        print("Nothing to save. Existing config left unchanged.")
        return
    os.makedirs(os.path.dirname(_CONFIG), exist_ok=True)
    with open(_CONFIG, "w") as fh:
        json.dump(accounts, fh, indent=2)
    os.chmod(_CONFIG, 0o600)   # readable only by you
    print(f"Wrote {len(accounts)} account(s) to {_CONFIG}.")
    print("Restart Jarvis (or he'll pick it up next launch) and he'll watch your inbox.")


if __name__ == "__main__":
    main()
