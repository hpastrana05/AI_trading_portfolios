import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import config
import timeutil


def _snapshots_path() -> Path:
    return config.env_data_dir() / "portfolio_snapshots.jsonl"


def _capital_path() -> Path:
    return config.env_data_dir() / "performance_capital.json"


def _default_baseline() -> float:
    if config.T212_ENV == "DEMO":
        return float(config.DEMO_BASELINE)
    return 0.0


def _demo_start_dt() -> datetime:
    """DEMO portfolio origin day (local app timezone, start of day)."""
    raw = (getattr(config, "DEMO_START_DATE", None) or "2026-07-30").strip()
    try:
        day = datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        day = datetime(2026, 7, 30).date()
    return datetime(
        day.year, day.month, day.day, 0, 0, 0, tzinfo=timeutil.app_tz()
    )


def _demo_start_iso() -> str:
    return _demo_start_dt().isoformat()


def load_capital() -> dict:
    path = _capital_path()
    data = {
        "baseline": _default_baseline() if config.T212_ENV == "DEMO" else None,
        "net_deposits": 0.0,
        "deposits": [],
        "updated_at": None,
    }
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw.get("baseline") is not None:
                data["baseline"] = float(raw["baseline"])
            data["net_deposits"] = float(raw.get("net_deposits") or 0)
            data["deposits"] = list(raw.get("deposits") or [])
            data["updated_at"] = raw.get("updated_at")
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass
    if data["baseline"] is None and config.T212_ENV == "DEMO":
        data["baseline"] = _default_baseline()
    return data


def save_capital(data: dict) -> dict:
    payload = {
        "baseline": data.get("baseline"),
        "net_deposits": float(data.get("net_deposits") or 0),
        "deposits": list(data.get("deposits") or []),
        "updated_at": timeutil.now_iso(),
    }
    _capital_path().write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def ensure_capital(account: dict | None = None) -> dict:
    """Ensure DEMO has 5000 baseline; LIVE sets baseline from first snapshot value."""
    capital = load_capital()
    changed = False
    if capital.get("baseline") is None:
        if config.T212_ENV == "DEMO":
            capital["baseline"] = _default_baseline()
            changed = True
        elif account and float(account.get("total_value") or 0) > 0:
            capital["baseline"] = float(account["total_value"])
            changed = True
    if changed:
        save_capital(capital)
    return capital


def add_deposit(amount: float, reason: str = "manual") -> dict:
    amount = float(amount)
    if amount <= 0:
        raise ValueError("Deposit amount must be > 0")
    capital = load_capital()
    if capital.get("baseline") is None:
        raise ValueError("Set a performance baseline before registering deposits")
    capital["net_deposits"] = float(capital.get("net_deposits") or 0) + amount
    deposits = list(capital.get("deposits") or [])
    deposits.append(
        {
            "timestamp": timeutil.now_iso(),
            "amount": amount,
            "reason": reason,
            "type": "deposit",
        }
    )
    capital["deposits"] = deposits
    return save_capital(capital)


def add_withdrawal(amount: float, reason: str = "manual") -> dict:
    """Record cash taken out so it does not look like a trading loss."""
    amount = float(amount)
    if amount <= 0:
        raise ValueError("Withdrawal amount must be > 0")
    capital = load_capital()
    if capital.get("baseline") is None:
        raise ValueError("Set a performance baseline before registering withdrawals")
    capital["net_deposits"] = float(capital.get("net_deposits") or 0) - amount
    deposits = list(capital.get("deposits") or [])
    deposits.append(
        {
            "timestamp": timeutil.now_iso(),
            "amount": -amount,
            "reason": reason,
            "type": "withdrawal",
        }
    )
    capital["deposits"] = deposits
    return save_capital(capital)


def _net_invested_at(capital: dict, when: datetime) -> float:
    baseline = float(capital.get("baseline") or 0)
    total = baseline
    for dep in capital.get("deposits") or []:
        try:
            ts = _parse_ts(dep["timestamp"])
            if ts <= when:
                total += float(dep.get("amount") or 0)
        except (KeyError, TypeError, ValueError):
            continue
    return total


def _last_snapshot() -> dict | None:
    rows = _read_snapshots()
    return rows[-1] if rows else None


def record_snapshot(account: dict) -> None:
    total_value = float(account.get("total_value", 0))
    if total_value <= 0:
        return

    capital = ensure_capital(account)
    previous = _last_snapshot()

    # Avoid duplicate spam: skip if last point is within 30s and value unchanged.
    if previous:
        try:
            age = (timeutil.now() - _parse_ts(previous["timestamp"])).total_seconds()
            same_value = abs(float(previous["total_value"]) - total_value) < 0.01
            if age < 30 and same_value:
                return
        except (TypeError, ValueError):
            pass

    # LIVE with no baseline yet after first point
    if capital.get("baseline") is None:
        capital["baseline"] = total_value
        save_capital(capital)

    payload = {
        "timestamp": timeutil.now_iso(),
        "total_value": total_value,
        "cash_available": float(account.get("cash_available") or 0),
        "account_total": float(account.get("account_total") or 0),
        "currency": account.get("currency", ""),
        "env": config.T212_ENV,
        "scope": "investable",
    }
    with open(_snapshots_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")


def _parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(timeutil.app_tz())


def _read_snapshots() -> list[dict]:
    path = _snapshots_path()
    if not path.exists():
        return []

    rows: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                row["total_value"] = float(row.get("total_value", 0))
                if row["total_value"] <= 0:
                    continue
                row["timestamp"] = str(row.get("timestamp", ""))
                row["cash_available"] = float(row.get("cash_available") or 0)
                rows.append(row)
            except (ValueError, json.JSONDecodeError, TypeError):
                continue

    rows.sort(key=lambda r: r["timestamp"])
    return rows


def _start_for_range(range_key: str, now: datetime) -> datetime | None:
    key = (range_key or "max").lower()
    if key == "1d":
        return now - timedelta(days=1)
    if key == "1w":
        return now - timedelta(weeks=1)
    if key == "1m":
        return now - timedelta(days=30)
    if key == "3m":
        return now - timedelta(days=90)
    if key == "1y":
        return now - timedelta(days=365)
    return None


def _max_drawdown(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0

    peak = values[0]
    worst_abs = 0.0
    worst_pct = 0.0

    for value in values:
        if value > peak:
            peak = value
        drawdown_abs = peak - value
        drawdown_pct = (drawdown_abs / peak * 100) if peak > 0 else 0.0
        if drawdown_abs > worst_abs:
            worst_abs = drawdown_abs
        if drawdown_pct > worst_pct:
            worst_pct = drawdown_pct

    return worst_abs, worst_pct


def build_performance(range_key: str = "max") -> dict:
    capital = ensure_capital()
    snapshots = _read_snapshots()
    currency = ""
    if snapshots:
        currency = snapshots[-1].get("currency", "")

    # DEMO: synthetic start at baseline on DEMO_START_DATE (default 2026-07-30).
    points_src = list(snapshots)
    if config.T212_ENV == "DEMO" and capital.get("baseline"):
        baseline = float(capital["baseline"])
        seed_ts = _demo_start_iso()
        has_seed = bool(
            points_src
            and abs(float(points_src[0]["total_value"]) - baseline) < 0.01
            and str(points_src[0].get("timestamp", "")).startswith(seed_ts[:10])
        )
        if not has_seed:
            # Drop a previous synthetic seed if present with wrong date/value.
            if (
                points_src
                and abs(float(points_src[0]["total_value"]) - baseline) < 0.01
                and len(points_src) > 1
            ):
                # Keep real history; replace only the leading synthetic-looking point
                # when it's clearly the old "1s before first snapshot" seed.
                try:
                    gap = (
                        _parse_ts(points_src[1]["timestamp"])
                        - _parse_ts(points_src[0]["timestamp"])
                    ).total_seconds()
                    if gap < 5:
                        points_src = points_src[1:]
                except (TypeError, ValueError):
                    pass
            points_src = [
                {
                    "timestamp": seed_ts,
                    "total_value": baseline,
                    "cash_available": baseline,
                    "currency": currency or "EUR",
                }
            ] + points_src

    if not points_src:
        return {
            "range": range_key.lower(),
            "currency": currency,
            "points": [],
            "capital": {
                "baseline": capital.get("baseline"),
                "net_deposits": float(capital.get("net_deposits") or 0),
                "net_invested": float(capital.get("baseline") or 0)
                + float(capital.get("net_deposits") or 0),
                "start_date": config.DEMO_START_DATE if config.T212_ENV == "DEMO" else None,
            },
            "metrics": {
                "total_pnl": 0.0,
                "total_pnl_pct": 0.0,
                "max_dd": 0.0,
                "max_dd_pct": 0.0,
            },
        }

    now = timeutil.now()
    start = _start_for_range(range_key, now)
    if start is not None:
        filtered = [r for r in points_src if _parse_ts(r["timestamp"]) >= start]
        # Keep one point before the window so range P&L is meaningful.
        earlier = [r for r in points_src if _parse_ts(r["timestamp"]) < start]
        if earlier and (not filtered or earlier[-1]["timestamp"] != filtered[0]["timestamp"]):
            filtered = [earlier[-1]] + filtered
    else:
        filtered = points_src

    if not filtered:
        filtered = [points_src[-1]]

    points = []
    invested_series: list[float] = []
    for row in filtered:
        when = _parse_ts(row["timestamp"])
        invested = _net_invested_at(capital, when)
        if invested <= 0:
            invested = float(row["total_value"])
        total = float(row["total_value"])
        pnl = total - invested
        pnl_pct = (pnl / invested * 100) if invested > 0 else 0.0
        invested_series.append(invested + pnl)  # = total_value, for DD on equity
        points.append(
            {
                "timestamp": row["timestamp"],
                "total_value": total,
                "net_invested": invested,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
            }
        )

    # Drawdown on deposit-adjusted equity (total_value), same as market equity.
    max_dd, max_dd_pct = _max_drawdown([p["total_value"] for p in points])
    # Prefer drawdown on P&L curve relative to net invested peak of (invested+pnl)=value
    # Already using total_value which is correct for portfolio equity DD.

    total_pnl = points[-1]["pnl"] if points else 0.0
    total_pnl_pct = points[-1]["pnl_pct"] if points else 0.0
    net_invested_now = points[-1]["net_invested"] if points else float(
        capital.get("baseline") or 0
    ) + float(capital.get("net_deposits") or 0)

    return {
        "range": range_key.lower(),
        "currency": filtered[-1].get("currency", currency),
        "points": points,
        "capital": {
            "baseline": capital.get("baseline"),
            "net_deposits": float(capital.get("net_deposits") or 0),
            "net_invested": net_invested_now,
            "start_date": config.DEMO_START_DATE if config.T212_ENV == "DEMO" else None,
        },
        "metrics": {
            "total_pnl": total_pnl,
            "total_pnl_pct": total_pnl_pct,
            "max_dd": max_dd,
            "max_dd_pct": max_dd_pct,
        },
    }


def reset_demo_local_data() -> dict:
    """Wipe DEMO-only local state after a Trading212 practice account reset."""
    if config.T212_ENV != "DEMO":
        raise RuntimeError("Reset is only available in DEMO")

    try:
        import autopilot

        autopilot.stop()
    except Exception:
        pass

    folder = config.env_data_dir("DEMO")
    removed = []
    names = [
        "portfolio_snapshots.jsonl",
        "portfolio_snapshots_full_account.jsonl",
        "performance_capital.json",
        ".snapshots_investable",
        "trades.jsonl",
        "ai_memory.json",
        "decisions.jsonl",
        "autopilot_state.json",
        "user_guidance.json",
        "instruments_cache.json",
    ]
    for name in names:
        path = folder / name
        if path.exists():
            path.unlink()
            removed.append(name)

    # Restore fresh DEMO baseline.
    save_capital(
        {
            "baseline": _default_baseline(),
            "net_deposits": 0.0,
            "deposits": [],
        }
    )
    return {"removed": removed, "baseline": _default_baseline()}
