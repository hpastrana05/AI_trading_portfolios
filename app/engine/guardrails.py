import json
from pathlib import Path

import config
import timeutil

DEFAULT_GUARDRAILS = {
    "max_position_pct": None,  # percent 0-100, None = AI decides
    "min_cash_pct": None,  # percent 0-100
    "max_trades_per_day": None,  # int
    "max_order_amount": None,  # absolute currency units
    # Drawdown circuit breaker (None = disabled)
    "safe_dd_pct": None,  # % from equity peak → force low risk + defensive cash
    "stop_dd_pct": None,  # % from equity peak → hard-stop autopilot
    "safe_min_cash_pct": 50,  # min cash while SAFE MODE is active
}


def _path() -> Path:
    return config.env_data_dir() / "guardrails.json"


def load() -> dict:
    path = _path()
    data = dict(DEFAULT_GUARDRAILS)
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            for key in DEFAULT_GUARDRAILS:
                if key in raw:
                    data[key] = _normalize(key, raw[key])
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass
    return data


def save(data: dict) -> dict:
    cleaned = {key: _normalize(key, data.get(key)) for key in DEFAULT_GUARDRAILS}
    safe_dd = cleaned.get("safe_dd_pct")
    stop_dd = cleaned.get("stop_dd_pct")
    if safe_dd is not None and stop_dd is not None and float(stop_dd) <= float(safe_dd):
        raise ValueError("stop_dd_pct must be greater than safe_dd_pct")
    cleaned["updated_at"] = timeutil.now_iso()
    _path().write_text(json.dumps(cleaned, indent=2), encoding="utf-8")
    return cleaned


def _normalize(key: str, value):
    if value is None or value == "" or str(value).strip().lower() in {"null", "none", "ai"}:
        # safe_min_cash_pct defaults to 50 when blank (still used only in safe mode)
        if key == "safe_min_cash_pct":
            return 50.0
        return None
    if key in (
        "max_position_pct",
        "min_cash_pct",
        "max_order_amount",
        "safe_dd_pct",
        "stop_dd_pct",
        "safe_min_cash_pct",
    ):
        num = float(value)
        if key.endswith("_pct") or key in ("safe_dd_pct", "stop_dd_pct", "safe_min_cash_pct"):
            if num < 0 or num > 100:
                raise ValueError(f"{key} must be between 0 and 100")
        elif num < 0:
            raise ValueError(f"{key} must be >= 0")
        return num
    if key == "max_trades_per_day":
        num = int(float(value))
        if num < 0:
            raise ValueError("max_trades_per_day must be >= 0")
        return num
    return value


def prompt_text(rules: dict | None = None) -> str:
    rules = rules or load()
    lines = []
    if rules.get("max_position_pct") is not None:
        lines.append(f"- Max position size: {rules['max_position_pct']}% of portfolio")
    if rules.get("min_cash_pct") is not None:
        lines.append(f"- Minimum cash: {rules['min_cash_pct']}% of portfolio")
    if rules.get("max_trades_per_day") is not None:
        lines.append(f"- Max trades per day: {rules['max_trades_per_day']}")
    if rules.get("max_order_amount") is not None:
        lines.append(f"- Max single order amount: {rules['max_order_amount']}")
    if rules.get("safe_dd_pct") is not None:
        lines.append(
            f"- Safe mode if drawdown from peak ≥ {rules['safe_dd_pct']}% "
            f"(min cash {rules.get('safe_min_cash_pct', 50)}%)"
        )
    if rules.get("stop_dd_pct") is not None:
        lines.append(f"- Hard stop if drawdown from peak ≥ {rules['stop_dd_pct']}%")
    if not lines:
        return "Hard guardrails: none (you decide sizing and cash)."
    return "Hard guardrails (must respect):\n" + "\n".join(lines)


def apply_to_allocation(allocation: dict[str, float], rules: dict | None = None) -> tuple[dict[str, float], list[str]]:
    """Clamp AI allocation to optional hard limits. Returns (allocation, notes)."""
    rules = rules or load()
    notes: list[str] = []
    alloc = {k: float(v) for k, v in allocation.items()}
    if not alloc:
        return alloc, notes

    max_pos = rules.get("max_position_pct")
    if max_pos is not None:
        cap = float(max_pos) / 100.0
        overflow = 0.0
        for ticker, weight in list(alloc.items()):
            if ticker == "CASH":
                continue
            if weight > cap:
                overflow += weight - cap
                alloc[ticker] = cap
                notes.append(f"Capped {ticker} to {max_pos}%")
        alloc["CASH"] = float(alloc.get("CASH", 0.0)) + overflow

    min_cash = rules.get("min_cash_pct")
    if min_cash is not None:
        floor = float(min_cash) / 100.0
        cash = float(alloc.get("CASH", 0.0))
        if cash < floor:
            need = floor - cash
            others = {k: v for k, v in alloc.items() if k != "CASH" and v > 0}
            total_others = sum(others.values())
            if total_others > 0 and need > 0:
                for ticker, weight in others.items():
                    cut = need * (weight / total_others)
                    alloc[ticker] = max(0.0, weight - cut)
                alloc["CASH"] = floor
                notes.append(f"Raised cash to min {min_cash}%")

    total = sum(alloc.values())
    if total > 0:
        alloc = {k: v / total for k, v in alloc.items()}
    return alloc, notes


def filter_trades(
    trades: list[dict],
    rules: dict | None = None,
    trades_today: int = 0,
) -> tuple[list[dict], list[str]]:
    rules = rules or load()
    notes: list[str] = []
    kept: list[dict] = []

    max_day = rules.get("max_trades_per_day")
    remaining = None if max_day is None else max(0, int(max_day) - int(trades_today))

    max_amount = rules.get("max_order_amount")

    for trade in trades:
        if remaining is not None and remaining <= 0:
            notes.append("Hit max_trades_per_day — remaining orders blocked")
            break

        amount = abs(float(trade.get("amount") or trade["quantity"] * trade["price"]))
        if max_amount is not None and amount > float(max_amount):
            price = float(trade["price"])
            if price <= 0:
                notes.append(f"Skipped {trade['ticker']}: exceeds max_order_amount")
                continue
            sign = 1 if trade["quantity"] > 0 else -1
            qty = round((float(max_amount) / price) * sign, 4)
            if abs(qty) < 0.0001:
                notes.append(f"Skipped {trade['ticker']}: below min size after max_order_amount")
                continue
            trade = {**trade, "quantity": qty, "amount": qty * price}
            notes.append(f"Scaled {trade['ticker']} to max_order_amount {max_amount}")

        kept.append(trade)
        if remaining is not None:
            remaining -= 1

    return kept, notes
