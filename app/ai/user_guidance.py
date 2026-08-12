import json
from pathlib import Path

from app.core import config, timeutil


def _path() -> Path:
    return config.env_data_dir() / "user_guidance.json"


def load() -> dict:
    path = _path()
    data = {"text": "", "updated_at": None}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            text = raw.get("text") or ""
            if not isinstance(text, str):
                text = str(text)
            data["text"] = text.strip()
            data["updated_at"] = raw.get("updated_at")
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass
    return data


def save(text: str) -> dict:
    cleaned = (text or "").strip()
    data = {
        "text": cleaned,
        "updated_at": timeutil.now_iso() if cleaned else None,
    }
    if cleaned:
        _path().write_text(json.dumps(data, indent=2), encoding="utf-8")
    else:
        clear()
    return data


def clear() -> None:
    path = _path()
    if path.exists():
        try:
            path.unlink()
        except OSError:
            path.write_text(
                json.dumps({"text": "", "updated_at": None}, indent=2),
                encoding="utf-8",
            )


def prompt_text(data: dict | None = None) -> str:
    """Return strategy snippet for the next cycle, or empty if none."""
    data = data or load()
    text = (data.get("text") or "").strip()
    if not text:
        return ""
    return (
        "User guidance for this cycle (follow unless it conflicts with hard guardrails):\n"
        f"{text}"
    )
