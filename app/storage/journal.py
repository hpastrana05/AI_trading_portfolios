import json

import config
import timeutil


def _trades_path():
    return config.env_data_dir() / "trades.jsonl"


def log_trade(entry: dict) -> None:
    entry = dict(entry)
    entry.setdefault("timestamp", timeutil.now_iso())
    entry.setdefault("where", config.T212_ENV)
    entry.setdefault("env", config.T212_ENV)
    with open(_trades_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def list_trades(limit: int = 200) -> list[dict]:
    path = _trades_path()
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    trades = []
    for line in lines[-limit:]:
        try:
            trades.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(trades))


def count_trades_today() -> int:
    today = timeutil.now().date().isoformat()
    count = 0
    for trade in list_trades(limit=1000):
        ts = str(trade.get("timestamp") or "")
        local = timeutil.format_local(ts, "%Y-%m-%d")
        if local == today:
            count += 1
    return count
