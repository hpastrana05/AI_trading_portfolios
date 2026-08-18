import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.core import config, timeutil


def _snapshots_path() -> Path:
    return config.env_data_dir() / "portfolio_snapshots.jsonl"


def _capital_path() -> Path:
    return config.env_data_dir() / "performance_capital.json"


def _default_baseline() -> float:
    if config.T212_ENV == "DEMO":
        return float(config.DEMO_BASELINE)
    return 0.0


def _demo_start_dt(capital: dict | None = None) -> datetime:
    """DEMO portfolio origin day (local app timezone, start of day)."""
    raw = ""
    if capital and capital.get("start_date"):
        raw = str(capital.get("start_date") or "").strip()
    if not raw:
        raw = (getattr(config, "DEMO_START_DATE", None) or "2026-07-30").strip()
    try:
        day = datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        day = datetime(2026, 7, 30).date()
    return datetime(
        day.year, day.month, day.day, 0, 0, 0, tzinfo=timeutil.app_tz()
    )


def _demo_start_iso(capital: dict | None = None) -> str:
    return _demo_start_dt(capital).isoformat()


def _capital_start_date(raw: dict | None = None) -> str | None:
    if raw and raw.get("start_date"):
        return str(raw["start_date"])[:10]
    if config.T212_ENV == "DEMO":
        return str(config.DEMO_START_DATE)[:10]
    return None


def _sum_deposit_amounts(capital: dict) -> float:
    total = 0.0
    for dep in capital.get("deposits") or []:
        try:
            total += float(dep.get("amount") or 0)
        except (TypeError, ValueError):
            continue
    return total


def load_capital() -> dict:
    path = _capital_path()
    data = {
        "baseline": _default_baseline() if config.T212_ENV == "DEMO" else None,
        "net_deposits": 0.0,
        "deposits": [],
        "updated_at": None,
        "start_date": _capital_start_date(),
    }
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw.get("baseline") is not None:
                data["baseline"] = float(raw["baseline"])
            data["deposits"] = list(raw.get("deposits") or [])
            data["updated_at"] = raw.get("updated_at")
            data["start_date"] = _capital_start_date(raw)
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass
    if data["baseline"] is None and config.T212_ENV == "DEMO":
        data["baseline"] = _default_baseline()
    # Always derive net from the deposit list (avoids stale doubled net_deposits).
    data["net_deposits"] = _sum_deposit_amounts(data)
    return data


def save_capital(data: dict) -> dict:
    deposits = list(data.get("deposits") or [])
    # Keep net_deposits consistent with the sum of recorded cashflows.
    net = 0.0
    for dep in deposits:
        try:
            net += float(dep.get("amount") or 0)
        except (TypeError, ValueError):
            continue
    payload = {
        "baseline": data.get("baseline"),
        "net_deposits": net,
        "deposits": deposits,
        "updated_at": timeutil.now_iso(),
        "start_date": data.get("start_date") or _capital_start_date(),
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
            "timestamp": _cashflow_timestamp(amount),
            "amount": amount,
            "reason": reason,
            "type": "deposit",
        }
    )
    capital["deposits"] = deposits
    saved = save_capital(capital)
    _adjust_protection_peak_for_cashflow(amount)
    return saved


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
            "timestamp": _cashflow_timestamp(-amount),
            "amount": -amount,
            "reason": reason,
            "type": "withdrawal",
        }
    )
    capital["deposits"] = deposits
    saved = save_capital(capital)
    _adjust_protection_peak_for_cashflow(-amount)
    return saved


def _cashflow_timestamp(signed_amount: float) -> str:
    """Stamp cashflows just before a matching portfolio jump so the chart stays flat."""
    anchor = _find_matching_jump_ts(signed_amount, _read_snapshots())
    return anchor or timeutil.now_iso()


def _find_matching_jump_ts(
    signed_amount: float,
    snapshots: list[dict],
    *,
    used_indices: set[int] | None = None,
    near: datetime | None = None,
    max_age_hours: float = 168,
) -> str | None:
    """Find a consecutive snapshot jump ≈ signed_amount; return ISO just before the post-jump point."""
    if abs(signed_amount) < 0.01 or len(snapshots) < 2:
        return None
    used_indices = used_indices if used_indices is not None else set()
    ref = near or timeutil.now()
    best_i: int | None = None
    best_ts: str | None = None
    best_score: tuple[float, float] | None = None

    for i in range(1, len(snapshots)):
        if i in used_indices:
            continue
        try:
            prev_v = float(snapshots[i - 1]["total_value"])
            cur_v = float(snapshots[i]["total_value"])
            delta = cur_v - prev_v
            when = _parse_ts(snapshots[i]["timestamp"])
        except (TypeError, ValueError, KeyError):
            continue
        if signed_amount * delta <= 0:
            continue
        abs_err = abs(delta - signed_amount)
        rel = abs_err / max(abs(signed_amount), 1.0)
        if rel > 0.2 and abs_err > 10:
            continue
        age_s = abs((ref - when).total_seconds())
        if age_s > max_age_hours * 3600:
            continue
        score = (rel, age_s)
        if best_score is None or score < best_score:
            best_score = score
            best_i = i
            best_ts = (when - timedelta(seconds=1)).isoformat()

    if best_i is None or best_ts is None:
        return None
    used_indices.add(best_i)
    return best_ts


def _align_deposits_to_jumps(capital: dict, snapshots: list[dict]) -> dict:
    """In-memory: snap deposit/withdrawal timestamps onto matching value jumps (heals old spikes)."""
    deps = [dict(d) for d in (capital.get("deposits") or [])]
    if not deps or len(snapshots) < 2:
        return {**capital, "deposits": deps}

    used: set[int] = set()
    aligned: list[dict] = []
    for dep in deps:
        out = dict(dep)
        try:
            amount = float(dep.get("amount") or 0)
            dep_ts = _parse_ts(dep["timestamp"])
        except (TypeError, ValueError, KeyError):
            aligned.append(out)
            continue
        if abs(amount) < 0.01:
            aligned.append(out)
            continue
        anchor = _find_matching_jump_ts(
            amount,
            snapshots,
            used_indices=used,
            near=dep_ts,
            max_age_hours=24 * 14,
        )
        if anchor:
            out["timestamp"] = anchor
        aligned.append(out)

    return {**capital, "deposits": aligned}


def _adjust_protection_peak_for_cashflow(signed_amount: float) -> None:
    """Keep circuit-breaker peak on trading equity (exclude deposits/withdrawals)."""
    try:
        from app.engine import autopilot

        state = autopilot.load_state()
        peak = state.get("equity_peak")
        if peak is None:
            return
        # Deposit raised raw equity (and maybe peak); subtract it back.
        # Withdrawal lowered raw equity; add it back so peak isn't artificially low.
        state["equity_peak"] = max(0.0, float(peak) - float(signed_amount))
        autopilot.save_state(state)
    except Exception:
        pass


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


def _net_deposits_at(capital: dict, when: datetime) -> float:
    total = 0.0
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


def last_snapshot_before_today() -> dict | None:
    """Most recent investable snapshot strictly before local midnight today."""
    today_start = timeutil.now().replace(hour=0, minute=0, second=0, microsecond=0)
    best = None
    for row in _read_snapshots():
        try:
            ts = _parse_ts(row["timestamp"])
        except (TypeError, ValueError):
            continue
        if ts < today_start:
            best = row
    return best


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
        seed_ts = _demo_start_iso(capital)
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

    # Align cashflows to portfolio jumps so deposit registration timing does not spike P&L.
    capital = _align_deposits_to_jumps(capital, points_src)

    if not points_src:
        return {
            "range": range_key.lower(),
            "currency": currency,
            "points": [],
            "capital": {
                "baseline": capital.get("baseline"),
                "net_deposits": _sum_deposit_amounts(capital),
                "net_invested": float(capital.get("baseline") or 0)
                + _sum_deposit_amounts(capital),
                "start_date": capital.get("start_date")
                if config.T212_ENV == "DEMO"
                else None,
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
    adjusted_series: list[float] = []
    baseline = float(capital.get("baseline") or 0)
    for row in filtered:
        when = _parse_ts(row["timestamp"])
        invested = _net_invested_at(capital, when)
        if invested <= 0:
            invested = float(row["total_value"])
        total = float(row["total_value"])
        pnl = total - invested
        # Trading equity = portfolio minus external cashflows (same as baseline + pnl).
        adjusted = baseline + pnl
        adjusted_series.append(adjusted)
        points.append(
            {
                "timestamp": row["timestamp"],
                "total_value": total,
                "net_invested": invested,
                "adjusted_equity": adjusted,
                "pnl": pnl,
                "pnl_pct": 0.0,  # filled below on deposit-neutral base
            }
        )

    # % and period P&L are relative to the first point in the selected range
    # (for 1D/1W/… that includes the anchor point just before the window).
    adj0 = adjusted_series[0] if adjusted_series else 0.0
    pnl0 = points[0]["pnl"] if points else 0.0
    for i, point in enumerate(points):
        point["period_pnl"] = point["pnl"] - pnl0
        if adj0 > 0:
            point["pnl_pct"] = (adjusted_series[i] / adj0 - 1.0) * 100.0
        else:
            point["pnl_pct"] = 0.0

    # Drawdown on deposit-adjusted equity within the selected range.
    max_dd, max_dd_pct = _max_drawdown(adjusted_series)

    # Cards = P&L over the selected period (not lifetime since baseline).
    total_pnl = points[-1]["period_pnl"] if points else 0.0
    total_pnl_pct = points[-1]["pnl_pct"] if points else 0.0
    net_deposits = _sum_deposit_amounts(capital)
    net_invested_now = float(baseline) + net_deposits

    return {
        "range": range_key.lower(),
        "currency": filtered[-1].get("currency", currency),
        "points": points,
        "capital": {
            "baseline": capital.get("baseline"),
            "net_deposits": net_deposits,
            "net_invested": net_invested_now,
            "start_date": capital.get("start_date")
            if config.T212_ENV == "DEMO"
            else None,
        },
        "metrics": {
            "total_pnl": total_pnl,
            "total_pnl_pct": total_pnl_pct,
            "max_dd": max_dd,
            "max_dd_pct": max_dd_pct,
        },
    }


def reset_local_data(baseline: float | None = None) -> dict:
    """Wipe local AI memory, trades, and performance for the current environment."""
    try:
        from app.engine import autopilot

        autopilot.stop()
    except Exception:
        pass

    folder = config.env_data_dir()
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
    ]
    for name in names:
        path = folder / name
        if path.exists():
            path.unlink()
            removed.append(name)

    if baseline is None or float(baseline) <= 0:
        baseline = _default_baseline() if config.T212_ENV == "DEMO" else None
    else:
        baseline = float(baseline)

    saved = save_capital(
        {
            "baseline": baseline,
            "net_deposits": 0.0,
            "deposits": [],
            "start_date": timeutil.now().date().isoformat(),
        }
    )
    return {
        "removed": removed,
        "baseline": saved.get("baseline"),
        "start_date": saved.get("start_date"),
    }


def reset_demo_local_data() -> dict:
    """Back-compat wrapper for DEMO-only local wipes."""
    if config.T212_ENV != "DEMO":
        raise RuntimeError("Reset is only available in DEMO")
    return reset_local_data(baseline=_default_baseline())
