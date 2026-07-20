"""Text entry point: `python -m jarvis`. Reads input, runs it through the
brain, prints the streamed reply and any tool activity. This is the interface
every other tier (voice, heartbeat) gets verified against as a fallback.

With --with-heartbeat, an in-process heartbeat runs alongside the chat so
proactive notices surface live; without it, held notices are still shown on
startup (catch-up-on-return).
"""

from __future__ import annotations

from jarvis import audit, killswitch, notices
from jarvis.brain import Brain
from jarvis.gate import console_confirmer
from jarvis.heartbeat import Heartbeat


def _show_held_notices() -> None:
    pending = notices.pending()
    if pending:
        print("While you were away:")
        for n in pending:
            print(f"  🔔 #{n['id']} {n['text']}")
        print("(say \"dismiss #<n>\" to clear one)\n")


def main(with_heartbeat: bool = False) -> None:
    brain = Brain(confirmer=console_confirmer)
    _show_held_notices()

    hb = None
    if with_heartbeat:
        def announce(notice: dict) -> None:
            print(f"\n🔔 {notice['text']}  (#{notice['id']})\nyou> ", end="", flush=True)

        hb = Heartbeat(on_interrupt=announce)
        hb.start()

    print("Jarvis's up. Commands: 'pause'/'resume' (kill switch), 'cost', 'exit'.\n")
    while True:
        try:
            user_input = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nsee ya.")
            break

        if not user_input:
            continue
        low = user_input.lower()
        if low in {"exit", "quit"}:
            print("see ya.")
            break
        if low == "pause":
            killswitch.pause()
            if hb is not None:
                hb.pause()
            print("⏸  proactive behavior paused (kill switch on). You can still chat.")
            continue
        if low == "resume":
            killswitch.resume()
            if hb is not None:
                hb.resume()
            print("▶️  proactive behavior resumed.")
            continue
        if low == "cost":
            print(audit.cost_summary())
            continue

        print("jarvis> ", end="", flush=True)
        for event in brain.turn(user_input):
            if event["type"] == "text":
                print(event["text"], end="", flush=True)
            elif event["type"] == "tool":
                print(f"\n  [using {event['name']}…]\n", end="", flush=True)
            elif event["type"] == "error":
                print(f"[{event['text']}]", end="", flush=True)
        print()

    if hb is not None:
        hb.stop()


if __name__ == "__main__":
    main()
