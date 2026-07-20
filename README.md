# JARVIS — a voice-first AI assistant for macOS

An always-on, voice-controlled assistant that lives on your Mac. Say its name,
give a command, and it acts — opening apps, sending messages, reading your
calendar and email, playing music, searching the web, and holding a real
conversation. It answers out loud in a composed, JARVIS-style "sir" persona.

Built to be **fast** (common commands answer instantly, no AI round-trip),
**private** (speech, brain and voice can all run locally), and **free to run**
(no paid services required in local mode).

> Personal data (names, contacts, API keys, memory) is **not** included — this
> is the clean, shareable version. Add your own via `.env` and the config files.

---

## What it can do

- **Conversation** — ask anything; it reasons and answers aloud, and keeps
  listening for follow-ups without repeating the wake word.
- **Mac control** — open/close/list apps; system volume, battery, disk, Wi-Fi,
  uptime, displays.
- **Vision** — screenshots the screen so the model can read an error or
  summarise what you're looking at.
- **Calendar** — read today or any range across all calendars; create,
  reschedule, delete events.
- **Messages** — send iMessages (fuzzy contact matching, incl. transliteration),
  read recent texts, unsend the last; watches for incoming texts and reads them.
- **Calls** — place phone/FaceTime calls; accept, reject, hang up.
- **Email** — watches Gmail (primary inbox only — no promotions/spam) across
  multiple accounts and announces new mail; read on request.
- **Music** — play any song/artist on Spotify.
- **Notes** — create, append to, and delete Apple Notes; search your own notes.
- **Info** — weather (auto-locates), news headlines, web search, date/time.
- **Memory** — remembers durable facts and loads them into every conversation.
- **Routines** — one phrase kicks off a scripted multi-app morning setup.
- **Transcripts** — record a session and export it to a Word document.

## How it works

Three cooperating processes:

1. **Engine** — wake word, mic, brain, tools, a local HTTP server.
2. **Window** — a small pywebview UI (reactive sphere), spawned as a separate
   process so its WebView doesn't fight the mic for CoreAudio.
3. **Hotkey daemon** — global shortcuts that survive the engine quitting.

| Layer | Default | Alternative |
|---|---|---|
| Wake word | vosk (offline) | — |
| Speech→text | Deepgram (cloud) | local Whisper (`faster-whisper`) |
| Brain | Claude (`claude-haiku`) | local Ollama model (auto-fallback) |
| Text→speech | macOS `say` | any installed voice |

Adding a capability is one file: write a module with a `register(registry)`
function and list it in `jarvis/tools/__init__.py`. Nothing else changes.

## Setup

Requires macOS and Python 3.9+.

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

cp .env.example .env          # then add your keys
# download the vosk wake-word model into ./models/ (see jarvis/config.py WAKE_MODEL_DIR)

./.venv/bin/python -m jarvis            # text mode (type to chat)
./.venv/bin/python -m jarvis --app      # full voice assistant + window
```

Optional, all with graceful fallbacks if skipped:

- **Fully local / offline**: install [Ollama](https://ollama.com), `ollama pull
  qwen2.5:7b`, and set `JARVIS_STT_ENGINE=whisper`, `JARVIS_BRAIN=local`.
- **Gmail**: `./.venv/bin/python -m jarvis.setup_gmail` (uses app passwords).
- **Always-on app**: a compiled `launcher.c` + a launchd LaunchAgent start it at
  login (edit the hardcoded path in `launcher.c` first).

### macOS permissions it uses (granted once in System Settings)

Microphone · Accessibility (hotkeys, call answering) · Full Disk Access
(reading Messages / Contacts) · Screen Recording (vision) · Automation.

## Notes on the code

Speech is imperfect, so the assistant is built to act on *intent*, not exact
words: fuzzy matching for apps/contacts, a keyword fast-path for common
commands (no AI cost), and a confirmation gate for consequential actions. Many
comments document hard-won macOS/AppleScript quirks (locale-parsed dates,
Contacts auto-launch hangs, Spotify cold-start races) — kept deliberately.

## Disclaimer

A personal project shared as-is. It automates a real Mac; review the code
before running it, and mind the permissions it requests.
