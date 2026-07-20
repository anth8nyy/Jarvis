import sys
import warnings

# System Python ships LibreSSL, which urllib3 warns about on every import.
# Harmless here; keep the console clean.
warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")


def main() -> None:
    import os

    args = sys.argv[1:]
    # The hotkey daemon reuses the same Jarvis.app binary (whose launcher
    # hardcodes --app), selected by env var from its own LaunchAgent — so the
    # one Accessibility grant covers both processes. Check env BEFORE --app.
    if os.environ.get("JARVIS_MODE") == "hotkeyd":
        from jarvis.hotkeyd import run
        run()
    elif "--window" in args:
        # Internal: the native circle-window process, spawned by --app.
        url = args[args.index("--window") + 1]
        from jarvis.app.window import run_window
        run_window(url)
    elif "--app" in args:
        from jarvis.app.desktop import run
        run()
    elif "--heartbeat" in args:
        from jarvis.heartbeat_daemon import run
        run()
    elif "--voice" in args:
        from jarvis.voice.loop import run
        run()
    else:
        from jarvis.cli import main as text_main
        text_main(with_heartbeat="--with-heartbeat" in args)


if __name__ == "__main__":
    main()
