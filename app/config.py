"""
config.py - ALL settings for NOTIVOX live in this one file.

Every other file does `from app import config` and reads values like
config.SARVAM_API_KEY. If a setting ever changes, we change it in ONE place.
"""
import os                       # "os" lets Python read environment variables
from pathlib import Path        # "Path" is a handy way to work with folder/file paths

from dotenv import load_dotenv  # reads the .env file

# ---------------------------------------------------------------- folders
# __file__ = the path of THIS file (notivox/app/config.py)
# .parent  = the folder that contains it (notivox/app)
APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent              # notivox/
STATIC_DIR = PROJECT_DIR / "static"       # notivox/static  -> the web page files
FONTS_DIR = APP_DIR / "fonts"             # notivox/app/fonts -> fonts for the PDF

# ---------------------------------------------------------------- read .env
# Copies every line of notivox/.env (KEY=value) into the environment,
# so os.getenv("KEY") can read it below.
load_dotenv(PROJECT_DIR / ".env")

# ---------------------------------------------------------------- Sarvam AI
# os.getenv("NAME", "default") -> the value from .env, or the default if missing.
# .strip() removes accidental spaces around the value.
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "").strip()
SARVAM_BASE = "https://api.sarvam.ai"     # the address of Sarvam's servers
LLM_MODEL = "sarvam-105b"                 # the "brain" that understands and writes
STT_MODEL = "saaras:v4"                   # speech-to-text (your voice -> text)
STT_MODE = "codemix"                      # understands Hindi+English mixed speech
TTS_MODEL = "bulbul:v4-flash"             # text-to-speech (text -> agent's voice)
# THE ONE AND ONLY VOICE of NOTIVOX: Ishita ("ishita_enhi_companion"), the voice in test_voice.wav.
# It is used for EVERY language and NEVER replaced by another voice. If Sarvam cannot speak a
# sentence in this voice, the agent shows the text without speaking (instead of switching voice).
TTS_SPEAKER = "ishita_enhi_companion"

# ---------------------------------------------------------------- MongoDB
MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
MONGODB_DB = os.getenv("MONGODB_DB", "notivox").strip()

# ---------------------------------------------------------------- spending limits (rupees)
# float(...) turns the text "80" into the number 80.0
VOICE_CAP_INR = float(os.getenv("NOTIVOX_VOICE_CAP_INR", "80"))  # above this: stop Sarvam voice
HARD_CAP_INR = float(os.getenv("NOTIVOX_HARD_CAP_INR", "95"))    # above this: stop everything
DAILY_DEVICE_LIMIT = int(os.getenv("NOTIVOX_DAILY_DEVICE_LIMIT", "80"))  # Sarvam calls per device per day


def check() -> list[str]:
    """Return a list of problems with the settings (an empty list means all good)."""
    problems = []
    if not SARVAM_API_KEY:
        problems.append("SARVAM_API_KEY is missing in .env")
    if not MONGODB_URI:
        problems.append("MONGODB_URI is missing in .env")
    return problems


# This block runs ONLY when you start this file directly:  python -m app.config
# (it does NOT run when another file imports config)
if __name__ == "__main__":
    issues = check()
    if issues:
        print("Problems found:")
        for p in issues:
            print(" -", p)
    else:
        # We never print the real secrets - only the first 4 characters.
        print("Sarvam key :", SARVAM_API_KEY[:4] + "****  (found)")
        print("MongoDB URL:", MONGODB_URI[:14] + "****  (found)")
        print("Database   :", MONGODB_DB)
        print("Settings OK")
