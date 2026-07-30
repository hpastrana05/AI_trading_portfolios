import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config
import timeutil

SNAPSHOTS_PATH = config.DATA_DIR / "portfolio_snapshots.jsonl"


def record_snapshot(account: dict) -> None:
    total_value = float(account.get("total_value", 0))
    if total_value <= 0:
        return

    payload = {
        "timestamp": timeutil.now_iso(),
        "total_value": total_value,
        "currency": account.get("currency", ""),
    }
    with open(SNAPSHOTS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")


def _parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(timeutil.app_tz())


def _read_snapshots() -> list[dict]:
    if not SNAPSHOTS_PATH.exists():
        return []

    rows: list[dict] = []
    with open(SNAPSHOTS_PATH, "r", encoding="utf-8") as f:
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
    snapshots = _read_snapshots()
    if not snapshots:
        return {
            "range": range_key.lower(),
            "currency": "",
            "points": [],
            "metrics": {"total_pnl": 0.0, "total_pnl_pct": 0.0, "max_dd": 0.0, "max_dd_pct": 0.0},
        }

    now = timeutil.now()
    start = _start_for_range(range_key, now)
    if start is not None:
        filtered = [r for r in snapshots if _parse_ts(r["timestamp"]) >= start]
    else:
        filtered = snapshots

    if not filtered:
        filtered = [snapshots[-1]]

    baseline = filtered[0]["total_value"]
    points = []
    values: list[float] = []
    for row in filtered:
        total = float(row["total_value"])
        pnl = total - baseline
        pnl_pct = (pnl / baseline * 100) if baseline > 0 else 0.0
        values.append(total)
        points.append(
            {
                "timestamp": row["timestamp"],
                "total_value": total,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
            }
        )

    max_dd, max_dd_pct = _max_drawdown(values)
    total_pnl = points[-1]["pnl"] if points else 0.0
    total_pnl_pct = points[-1]["pnl_pct"] if points else 0.0

    return {
        "range": range_key.lower(),
        "currency": filtered[-1].get("currency", ""),
        "points": points,
        "metrics": {
            "total_pnl": total_pnl,
            "total_pnl_pct": total_pnl_pct,
            "max_dd": max_dd,
            "max_dd_pct": max_dd_pct,
        },
    }
