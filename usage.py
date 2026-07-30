import json
from datetime import datetime

import config
import timeutil


def _price_for_model(model: str) -> dict[str, float]:
    name = (model or "").lower()
    if "lite" in name:
        return config.GEMINI_PRICE_PER_MTOK["flash-lite"]
    if "flash" in name:
        return config.GEMINI_PRICE_PER_MTOK["flash"]
    return config.GEMINI_PRICE_PER_MTOK["default"]


def limits_for_model(model: str) -> dict[str, int]:
    name = (model or "").lower()
    for key, limits in config.GEMINI_FREE_LIMITS.items():
        if key != "default" and key in name:
            return dict(limits)
    return dict(config.GEMINI_FREE_LIMITS["default"])


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    prices = _price_for_model(model)
    return (input_tokens / 1_000_000) * prices["input"] + (output_tokens / 1_000_000) * prices["output"]


def log_call(
    *,
    model: str,
    purpose: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    ok: bool = True,
    error: str | None = None,
) -> None:
    cost = estimate_cost_usd(model, input_tokens, output_tokens)
    entry = {
        "timestamp": timeutil.now_iso(),
        "env": config.T212_ENV,
        "model": model,
        "purpose": purpose,
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "cost_usd": round(cost, 8),
        "ok": ok,
        "error": error,
    }
    with open(config.AI_USAGE_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _read_rows(limit: int | None = None) -> list[dict]:
    path = config.AI_USAGE_PATH
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    if limit:
        lines = lines[-limit:]
    rows = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _day_of(ts: str) -> str:
    try:
        return (
            datetime.fromisoformat(ts.replace("Z", "+00:00"))
            .astimezone(timeutil.app_tz())
            .date()
            .isoformat()
        )
    except ValueError:
        return ""


def today_call_count(rows: list[dict] | None = None) -> int:
    rows = rows if rows is not None else _read_rows()
    today = timeutil.now().date().isoformat()
    return sum(1 for row in rows if row.get("ok", True) and _day_of(str(row.get("timestamp") or "")) == today)


def preferred_model() -> str:
    if config.GEMINI_MODEL.lower() != "auto":
        return config.GEMINI_MODEL
    return config.GEMINI_MODEL_FALLBACKS[0] if config.GEMINI_MODEL_FALLBACKS else "gemini-3.5-flash-lite"


def can_afford_cycle(extra_calls: int | None = None) -> tuple[bool, str]:
    """Soft check against free-tier RPD for the preferred model."""
    extra = extra_calls if extra_calls is not None else config.AI_CALLS_PER_CYCLE
    model = preferred_model()
    limits = limits_for_model(model)
    used = today_call_count()
    rpd = int(limits["rpd"])
    if used + extra > rpd:
        return False, (
            f"Free-tier RPD budget tight for {model}: {used}/{rpd} used today "
            f"(cycle needs ~{extra} calls). Skipping AI cycle."
        )
    return True, f"{used}/{rpd} RPD used today on free tier ({model})"


def summary(limit: int = 5000) -> dict:
    rows = _read_rows(limit)
    today = timeutil.now().date().isoformat()
    total_calls = 0
    total_cost = 0.0
    today_calls = 0
    today_cost = 0.0
    today_tokens = 0
    by_model: dict[str, dict] = {}
    by_model_today: dict[str, int] = {}
    by_env: dict[str, dict] = {}
    recent = list(reversed(rows[-50:]))

    for row in rows:
        total_calls += 1
        cost = float(row.get("cost_usd") or 0)
        total_cost += cost
        model = row.get("model") or "unknown"
        env = row.get("env") or "?"
        by_model.setdefault(model, {"calls": 0, "cost_usd": 0.0, "today_calls": 0})
        by_model[model]["calls"] += 1
        by_model[model]["cost_usd"] += cost
        by_env.setdefault(env, {"calls": 0, "cost_usd": 0.0})
        by_env[env]["calls"] += 1
        by_env[env]["cost_usd"] += cost

        day = _day_of(str(row.get("timestamp") or ""))
        if day == today and row.get("ok", True):
            today_calls += 1
            today_cost += cost
            today_tokens += int(row.get("input_tokens") or 0) + int(row.get("output_tokens") or 0)
            by_model[model]["today_calls"] += 1
            by_model_today[model] = by_model_today.get(model, 0) + 1

    preferred = preferred_model()
    pref_limits = limits_for_model(preferred)
    rpd = int(pref_limits["rpd"])
    cycles_left = max(0, (rpd - today_calls) // config.AI_CALLS_PER_CYCLE)

    free_limits_table = []
    seen = set()
    for label, limits in config.GEMINI_FREE_LIMITS.items():
        if label == "default":
            continue
        key = (limits["rpm"], limits["tpm"], limits["rpd"])
        if key in seen:
            continue
        seen.add(key)
        # Sum today calls for models matching this limit family.
        used_today = 0
        for model, count in by_model_today.items():
            if limits_for_model(model) == limits:
                used_today += count
        free_limits_table.append(
            {
                "family": label,
                "rpm": limits["rpm"],
                "tpm": limits["tpm"],
                "rpd": limits["rpd"],
                "rpd_used": used_today,
                "rpd_left": max(0, limits["rpd"] - used_today),
            }
        )

    ok, budget_msg = can_afford_cycle()

    return {
        "total_calls": total_calls,
        "total_cost_usd": round(total_cost, 6),
        "today_calls": today_calls,
        "today_cost_usd": round(today_cost, 6),
        "today_tokens": today_tokens,
        "by_model": by_model,
        "by_env": by_env,
        "recent": recent,
        "preferred_model": preferred,
        "preferred_limits": pref_limits,
        "calls_per_cycle": config.AI_CALLS_PER_CYCLE,
        "cycles_left_today": cycles_left,
        "budget_ok": ok,
        "budget_msg": budget_msg,
        "free_limits": free_limits_table,
        "note": (
            "Free-tier limits from Google AI Studio (RPM/TPM/RPD). "
            "Each cycle uses 3 Gemini calls. Costs are estimates (free tier is $0)."
        ),
    }
