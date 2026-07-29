import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

T212_ENV = os.getenv("T212_ENV", "DEMO").upper()

T212_DEMO_API_KEY = os.getenv("T212_DEMO_API_KEY", "")
T212_DEMO_API_SECRET = os.getenv("T212_DEMO_API_SECRET", "")
T212_LIVE_API_KEY = os.getenv("T212_LIVE_API_KEY", "")
T212_LIVE_API_SECRET = os.getenv("T212_LIVE_API_SECRET", "")

if T212_ENV == "LIVE":
    API_KEY = T212_LIVE_API_KEY
    API_SECRET = T212_LIVE_API_SECRET
    API_LINK = "https://live.trading212.com/api/v0/"
else:
    API_KEY = T212_DEMO_API_KEY
    API_SECRET = T212_DEMO_API_SECRET
    API_LINK = "https://demo.trading212.com/api/v0/"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# Use "auto" to try models in order until one works (recommended).
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "auto")
GEMINI_MODEL_FALLBACKS = [
    m.strip()
    for m in os.getenv(
        "GEMINI_MODEL_FALLBACKS",
        "gemini-3.5-flash-lite,gemini-3.6-flash,gemini-3.1-flash-lite",
    ).split(",")
    if m.strip()
]


STRATEGY = os.getenv(
    "STRATEGY", "Conservative long-term growth, prefer ETFs, keep 10% cash"
)

MAX_POSITION_PCT = 0.25
MIN_CASH_PCT = 0.10
MAX_PICKS = int(os.getenv("MAX_PICKS", "12"))
AUTOPILOT_INTERVAL_MINUTES = int(os.getenv("AUTOPILOT_INTERVAL_MINUTES", "60"))

DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

INSTRUMENTS_CACHE_PATH = DATA_DIR / "instruments_cache.json"
INSTRUMENTS_CACHE_HOURS = int(os.getenv("INSTRUMENTS_CACHE_HOURS", "24"))

APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "8100"))
