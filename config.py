import os
import shutil
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
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "auto")
GEMINI_MODEL_FALLBACKS = [
    m.strip()
    for m in os.getenv(
        "GEMINI_MODEL_FALLBACKS",
        # Prefer high free-tier RPD models first (Flash Lite ~500/day).
        "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-2.5-flash",
    ).split(",")
    if m.strip()
]

# Free-tier rate limits (Google AI Studio). Used for usage UI + soft budget checks.
# Keys are matched as substrings against the model name (lowercased).
GEMINI_FREE_LIMITS = {
    "3.5-flash-lite": {"rpm": 15, "tpm": 250_000, "rpd": 500},
    "flash-lite": {"rpm": 15, "tpm": 250_000, "rpd": 500},
    "2.5-flash": {"rpm": 5, "tpm": 250_000, "rpd": 20},
    "default": {"rpm": 5, "tpm": 250_000, "rpd": 20},
}

# Each autopilot cycle makes this many Gemini requests.
AI_CALLS_PER_CYCLE = 3

# Approximate USD per 1M tokens (input/output) for cost estimates (paid tier reference).
GEMINI_PRICE_PER_MTOK = {
    "default": {"input": 0.10, "output": 0.40},
    "flash-lite": {"input": 0.10, "output": 0.40},
    "flash": {"input": 0.15, "output": 0.60},
}

STRATEGY = os.getenv(
    "STRATEGY", "Conservative long-term growth, prefer ETFs, keep 10% cash"
)

MAX_PICKS = int(os.getenv("MAX_PICKS", "12"))
AUTOPILOT_INTERVAL_MINUTES = int(os.getenv("AUTOPILOT_INTERVAL_MINUTES", "60"))

DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

_LEGACY_ENV_FILES = (
    "trades.jsonl",
    "ai_memory.json",
    "portfolio_snapshots.jsonl",
    "autopilot_state.json",
    "decisions.jsonl",
    "instruments_cache.json",
    "guardrails.json",
)


def env_data_dir(env: str | None = None) -> Path:
    """Per-environment data folder: data/demo or data/live."""
    name = (env or T212_ENV).upper()
    folder = "live" if name == "LIVE" else "demo"
    path = DATA_DIR / folder
    path.mkdir(parents=True, exist_ok=True)
    return path


def _migrate_legacy_demo_files() -> None:
    """Move old flat data/* files into data/demo/ once (previous DEMO usage)."""
    demo = env_data_dir("DEMO")
    for name in _LEGACY_ENV_FILES:
        src = DATA_DIR / name
        dst = demo / name
        if src.exists() and not dst.exists():
            shutil.move(str(src), str(dst))


_migrate_legacy_demo_files()

ENV_DATA_DIR = env_data_dir()

INSTRUMENTS_CACHE_PATH = ENV_DATA_DIR / "instruments_cache.json"
INSTRUMENTS_CACHE_HOURS = int(os.getenv("INSTRUMENTS_CACHE_HOURS", "24"))

# Shared across envs (same Gemini key).
AI_USAGE_PATH = DATA_DIR / "ai_usage.jsonl"

APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "8100"))
APP_TZ = os.getenv("APP_TZ", "Europe/Madrid")

# Sibling Demo/Live jump link:
# - OTHER_ENV_URL: absolute override (optional)
# - OTHER_ENV_PORT: same host as the browser, different port (preferred for LAN/VPN)
OTHER_ENV_URL = os.getenv("OTHER_ENV_URL", "").rstrip("/")
_other_port = os.getenv("OTHER_ENV_PORT", "").strip()
OTHER_ENV_PORT = int(_other_port) if _other_port.isdigit() else None
OTHER_ENV = "DEMO" if T212_ENV == "LIVE" else "LIVE"

# Performance: DEMO P&L is measured from this starting capital.
DEMO_BASELINE = float(os.getenv("DEMO_BASELINE", "5000"))
