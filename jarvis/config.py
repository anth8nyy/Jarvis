import os

from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# Haiku is much faster than Sonnet for these short, tool-routing turns — the
# single biggest lever on response latency. Override with JARVIS_MODEL if you
# want more reasoning power.
MODEL = os.environ.get("JARVIS_MODEL", "claude-haiku-4-5")
# Short spoken replies → smaller cap = faster responses.
MAX_TOKENS = int(os.environ.get("JARVIS_MAX_TOKENS", "160"))

# Speech-to-text (Deepgram). Text mode doesn't need it, so no hard-fail here.
DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY", "")

# nova-3 + keyterm prompting: names Deepgram would otherwise mangle ("Jeremy"
# for the wake word, or a contact name) get boosted, so the brain sees the right words.
STT_MODEL = os.environ.get("JARVIS_STT_MODEL", "nova-3")
# language=en, NOT multi: keyterm prompting is English-only, nova-3's `multi`
# doesn't cover Greek (it returns phonetic mush), and it measurably degraded
# English. Anything Greek is translated by the brain, not heard by Deepgram.
STT_LANGUAGE = os.environ.get("JARVIS_STT_LANGUAGE", "en")
# Words worth boosting: his name, the people texted most, the apps controlled.
# Add a name here whenever Deepgram keeps mishearing it.
STT_KEYTERMS = [
    "Jarvis",
    # add the names of people you text most here so STT spells them right
    "Spotify", "Claude", "ChatGPT", "Notes", "Calendar", "Messages",
]

# STT engine: "deepgram" (cloud) or "whisper" (local faster-whisper).
STT_ENGINE = os.environ.get("JARVIS_STT_ENGINE", "deepgram")

# Use Silero VAD for end-of-speech detection. Off = the simpler loudness
# threshold (the original behaviour).
USE_SILERO_VAD = os.environ.get("JARVIS_USE_VAD", "0") == "1"

# Let the user interrupt over the speakers (energy-spike detection). Off = only
# the wake word "Jarvis" interrupts him.
SPEAKER_BARGE_IN = os.environ.get("JARVIS_SPEAKER_BARGEIN", "0") == "1"

# The brain: "auto" = Claude (smart & fast, like the original) with an
# automatic fall-back to the local Ollama model ONLY if Claude is unreachable
# (out of tokens / no Wi-Fi), so Jarvis degrades gracefully instead of breaking;
# "claude" = cloud only; "local" = offline Ollama only.
BRAIN = os.environ.get("JARVIS_BRAIN", "auto")
OLLAMA_URL = os.environ.get("JARVIS_OLLAMA_URL", "http://127.0.0.1:11434")
LOCAL_MODEL = os.environ.get("JARVIS_LOCAL_MODEL", "qwen2.5:7b")
# Keep the model resident so commands don't hit a cold reload. Ollama wants a
# Go duration STRING here ("30m", "24h") — a bare "-1" 400s the chat endpoint
# with 'missing unit in duration'. A long duration is effectively "stay loaded".
LOCAL_KEEPALIVE = os.environ.get("JARVIS_LOCAL_KEEPALIVE", "24h")
# base.en ≈0.3s/command and accurate; small.en is better on heavy accents at
# ≈0.9s. int8 is the fast CPU path on Apple Silicon.
WHISPER_MODEL = os.environ.get("JARVIS_WHISPER_MODEL", "base.en")
WHISPER_COMPUTE = os.environ.get("JARVIS_WHISPER_COMPUTE", "int8")
WHISPER_DIR = os.environ.get(
    "JARVIS_WHISPER_DIR",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "whisper"),
)
# Decoder bias — the local equivalent of Deepgram keyterms. Seeds Whisper with
# the vocabulary it should expect so names aren't mangled.
WHISPER_PROMPT = os.environ.get(
    "JARVIS_WHISPER_PROMPT",
    "Jarvis, Spotify, Claude, Notes.",
)

# Text-to-speech: free, offline macOS `say`. Left blank, Jarvis auto-picks the
# best-quality British male voice actually installed (Premium > Enhanced >
# compact) — so he upgrades himself the moment you download a better one via
# System Settings › Accessibility › Spoken Content › System Voice › Manage
# Voices. Set JARVIS_VOICE to force a specific one (`say -v '?'` lists them).
JARVIS_VOICE = os.environ.get("JARVIS_VOICE", "")
JARVIS_SPEECH_RATE = int(os.environ.get("JARVIS_SPEECH_RATE", "190"))

# Preferred voice "families", best first. Premium/Enhanced variants of these
# are the ones that actually sound human rather than robotic.
VOICE_PREFERENCE = ["Jamie", "Oliver", "Daniel", "Arthur", "Malcolm"]

# Spotify Web API (play-by-name). Set these after creating a Spotify app at
# developer.spotify.com — see README/setup. Optional; the tool errors clearly
# if unset.
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
SPOTIFY_REDIRECT_URI = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")

# Offline wake word (vosk model dir).
WAKE_MODEL_DIR = os.environ.get(
    "JARVIS_WAKE_MODEL",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "models", "vosk-model-small-en-us-0.15"),
)
WAKE_PHRASE = os.environ.get("JARVIS_WAKE_PHRASE", "jarvis")

if not ANTHROPIC_API_KEY:
    raise RuntimeError(
        "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and fill it in."
    )
