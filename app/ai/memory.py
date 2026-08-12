import json
import re

import config
import timeutil


def _memory_path():
    return config.env_data_dir() / "ai_memory.json"


DEFAULT_MEMORY = {
    "portfolio_thesis": "Starting fresh. Build a diversified portfolio aligned with the selected risk level.",
    "management_plan": "Review portfolio periodically and rebalance toward target allocation.",
    "lessons": [],
    "notes": "",
    "thinking_log": [],
    "recent_skips": [],
    # When False, order failures are not stored/fed to the AI as ticker scars.
    "skip_tracking": False,
    "updated_at": None,
}

# Lessons/notes that falsely turn temporary failures into permanent ticker bans.
_BAN_LESSON_RE = re.compile(
    r"(do not use|never use|avoid .*ticker|banned|not allowed|invalid ticker|"
    r"strictly limited|exact allowed list|allowed tickers are)",
    re.I,
)


def load() -> dict:
    path = _memory_path()
    if not path.exists():
        return dict(DEFAULT_MEMORY)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        merged = dict(DEFAULT_MEMORY)
        merged.update(data)
        # Missing key in old files → disabled (feature is optional / off by default).
        if "skip_tracking" not in data:
            merged["skip_tracking"] = False
        else:
            merged["skip_tracking"] = bool(data.get("skip_tracking"))
        return merged
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_MEMORY)


def skip_tracking_enabled(data: dict | None = None) -> bool:
    data = data or load()
    return bool(data.get("skip_tracking"))


def set_skip_tracking(enabled: bool) -> dict:
    data = load()
    data["skip_tracking"] = bool(enabled)
    if not enabled:
        # Turning off also clears scars so they cannot linger in prompts later.
        data["recent_skips"] = []
        data["lessons"] = [
            lesson
            for lesson in _filter_ban_lessons(data.get("lessons") or [])
            if "Temporary skip on" not in str(lesson)
        ]
        notes = data.get("notes") or ""
        if "EXECUTION FACT" in notes:
            data["notes"] = (
                "Ticker skip tracking is off. Trust Current allocation for what is held."
            )
    save(data)
    return data



def save(data: dict) -> None:
    data["updated_at"] = timeutil.now_iso()
    _memory_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


def _filter_ban_lessons(lessons: list) -> list:
    cleaned = []
    for lesson in lessons or []:
        text = str(lesson).strip()
        if not text:
            continue
        if _BAN_LESSON_RE.search(text):
            continue
        cleaned.append(text)
    return cleaned[-20:]


def apply_update(update: dict, thinking: str = "") -> dict:
    data = load()
    for key in ("portfolio_thesis", "management_plan", "notes"):
        if update.get(key):
            text = str(update[key])
            # Strip AI attempts to lock a permanent allow-list into notes/plan.
            if key in ("notes", "management_plan") and _BAN_LESSON_RE.search(text):
                text = re.sub(
                    r"(?i).{0,80}(strictly limited|exact allowed list|allowed tickers are).{0,120}",
                    "",
                    text,
                ).strip() or data.get(key, "")
            data[key] = text
    if update.get("lessons"):
        existing = _filter_ban_lessons(data.get("lessons") or [])
        for lesson in update["lessons"]:
            if not lesson or _BAN_LESSON_RE.search(str(lesson)):
                continue
            text = str(lesson).strip()
            if text and text not in existing:
                existing.append(text)
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


def record_execution_feedback(
    executed: list[dict],
    skipped: list[dict],
    actual_allocation: dict[str, float],
) -> dict:
    """Record fills/skips without permanently banning tickers."""
    if not skipped and not executed:
        return load()

    data = load()
    track_skips = skip_tracking_enabled(data)
    lessons = _filter_ban_lessons(data.get("lessons") or [])
    recent = list(data.get("recent_skips") or []) if track_skips else []

    if track_skips:
        for item in skipped:
            ticker = item.get("ticker", "?")
            reason = item.get("reason", "unknown")
            lesson = (
                f"Temporary skip on {ticker}: {reason}. "
                "Position was NOT filled that cycle — do not claim it is held. "
                "You MAY retry this ticker later; skips are not permanent bans."
            )
            if lesson not in lessons:
                lessons.append(lesson)
            recent.append(
                {
                    "at": timeutil.now_iso(),
                    "ticker": ticker,
                    "reason": reason,
                }
            )

    for item in executed:
        ticker = item.get("ticker", "?")
        action = item.get("action", "trade")
        lesson = f"Executed {action} on {ticker} successfully."
        if lesson not in lessons:
            lessons.append(lesson)

    data["lessons"] = lessons[-20:]
    data["recent_skips"] = recent[-20:] if track_skips else []

    if track_skips and skipped:
        cash_pct = float(actual_allocation.get("CASH", 0.0)) * 100
        held = {
            k: round(float(v), 4)
            for k, v in actual_allocation.items()
            if k != "CASH" and float(v) > 0.001
        }
        skipped_tickers = ", ".join(
            sorted({str(s.get("ticker")) for s in skipped if s.get("ticker")})
        )
        data["notes"] = (
            f"EXECUTION FACT (this cycle only): {len(skipped)} order(s) were NOT filled "
            f"({skipped_tickers}). "
            f"Actual allocation now: {held or 'no stock/ETF positions'} "
            f"with cash ≈ {cash_pct:.1f}%. "
            f"Do not claim those tickers are held. Skips are temporary — retry is allowed."
        )

    save(data)
    return data


def skips_prompt_text(data: dict | None = None) -> str:
    data = data or load()
    if not skip_tracking_enabled(data):
        return ""
    skips = data.get("recent_skips") or []
    if not skips:
        return ""
    lines = []
    seen = set()
    for item in reversed(skips):
        ticker = item.get("ticker")
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        lines.append(f"- {ticker}: {item.get('reason', 'skipped')}")
        if len(lines) >= 8:
            break
    if not lines:
        return ""
    return (
        "Recent TEMPORARY order failures (NOT held that time — still eligible to retry; "
        "NOT a permanent ban):\n"
        + "\n".join(lines)
    )


def clear_ticker_scars() -> dict:
    """Remove skip history and ban-like lessons so the AI can pick freely again."""
    data = load()
    data["recent_skips"] = []
    data["lessons"] = _filter_ban_lessons(data.get("lessons") or [])
    notes = data.get("notes") or ""
    if "EXECUTION FACT" in notes or _BAN_LESSON_RE.search(notes):
        data["notes"] = (
            "Ticker universe is open each cycle. Temporary order failures are not bans. "
            "Trust Current allocation for what is actually held."
        )
    plan = data.get("management_plan") or ""
    if _BAN_LESSON_RE.search(plan) or "trust Current allocation only" in plan:
        data["management_plan"] = (
            "Each cycle may pick any suitable Trading212 symbols. "
            "Rebalance using Current allocation as truth; retry failed orders when appropriate."
        )
    save(data)
    return data
