import json

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
    "updated_at": None,
}


def load() -> dict:
    path = _memory_path()
    if not path.exists():
        return dict(DEFAULT_MEMORY)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        merged = dict(DEFAULT_MEMORY)
        merged.update(data)
        return merged
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_MEMORY)


def save(data: dict) -> None:
    data["updated_at"] = timeutil.now_iso()
    _memory_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


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


def record_execution_feedback(
    executed: list[dict],
    skipped: list[dict],
    actual_allocation: dict[str, float],
) -> dict:
    """Learn from fills vs skips so memory never claims unheld positions."""
    if not skipped and not executed:
        return load()

    data = load()
    lessons = list(data.get("lessons") or [])
    recent = list(data.get("recent_skips") or [])

    for item in skipped:
        ticker = item.get("ticker", "?")
        reason = item.get("reason", "unknown")
        lesson = (
            f"Skipped {ticker}: {reason}. "
            "Do not assume this ticker is held until it appears in Current allocation."
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
    data["recent_skips"] = recent[-20:]

    if skipped:
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
            f"EXECUTION FACT (authoritative, overrides prior assumptions): "
            f"{len(skipped)} order(s) were SKIPPED and NOT filled "
            f"({skipped_tickers}). "
            f"Actual allocation now: {held or 'no stock/ETF positions'} "
            f"with cash ≈ {cash_pct:.1f}%. "
            f"Never claim a skipped ticker is held. Prefer alternatives or smaller size next cycle."
        )
        plan = data.get("management_plan") or ""
        correction = (
            f" Next: retry with different sizing/tickers after skips ({skipped_tickers}); "
            "trust Current allocation only."
        )
        if "trust Current allocation only" not in plan:
            data["management_plan"] = (plan + correction).strip()

    save(data)
    return data


def skips_prompt_text(data: dict | None = None) -> str:
    data = data or load()
    skips = data.get("recent_skips") or []
    if not skips:
        return ""
    # Last few unique ticker skips for the next cycle prompt.
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
        "Recent SKIPPED orders (NOT held — do not allocate as if filled):\n"
        + "\n".join(lines)
    )
