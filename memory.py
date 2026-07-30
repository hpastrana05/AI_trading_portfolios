import json
from pathlib import Path

import config
import timeutil

MEMORY_PATH = config.DATA_DIR / "ai_memory.json"

DEFAULT_MEMORY = {
    "portfolio_thesis": "Starting fresh. Build a diversified portfolio aligned with the selected risk level.",
    "management_plan": "Review portfolio periodically and rebalance toward target allocation.",
    "lessons": [],
    "notes": "",
    "thinking_log": [],
    "updated_at": None,
}


def load() -> dict:
    if not MEMORY_PATH.exists():
        return dict(DEFAULT_MEMORY)
    try:
        data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
        merged = dict(DEFAULT_MEMORY)
        merged.update(data)
        return merged
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_MEMORY)


def save(data: dict) -> None:
    data["updated_at"] = timeutil.now_iso()
    MEMORY_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def apply_update(update: dict, thinking: str = "") -> dict:
    data = load()
    for key in ("portfolio_thesis", "management_plan", "notes"):
        if update.get(key):
            data[key] = update[key]
    if update.get("lessons"):
        existing = data.get("lessons") or []
        for lesson in update["lessons"]:
            if lesson and lesson not in existing:
                existing.append(lesson)
        data["lessons"] = existing[-20:]
    if thinking:
        log = data.get("thinking_log") or []
        log.append(
            {
                "at": timeutil.now_iso(),
                "text": thinking,
            }
        )
        data["thinking_log"] = log[-50:]
    save(data)
    return data
