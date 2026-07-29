import json
from datetime import datetime, timezone
from pathlib import Path

import config

TRADES_PATH = config.DATA_DIR / "trades.jsonl"


def log_trade(entry: dict) -> None:
    entry = dict(entry)
    entry.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    with open(TRADES_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def list_trades(limit: int = 200) -> list[dict]:
    if not TRADES_PATH.exists():
        return []
    lines = TRADES_PATH.read_text(encoding="utf-8").strip().splitlines()
    trades = []
    for line in lines[-limit:]:
        try:
            trades.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(trades))
