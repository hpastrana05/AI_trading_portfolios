import json
import time
from pathlib import Path

import config
from trading_api import accounts, instruments, orders, positions


def get_account() -> dict:
    data = accounts.get_account_summary()
    return {
        "total_value": float(data["totalValue"]),
        "currency": data.get("currency", ""),
        "cash_available": float(data["cash"]["availableToTrade"]),
    }


def get_positions() -> list[dict]:
    data = positions.get_all_open_positions()
    result = []
    for pos in data:
        qty = float(pos["quantity"])
        price = float(pos.get("currentPrice", 0))
        result.append(
            {
                "ticker": pos["instrument"]["ticker"],
                "quantity": qty,
                "current_price": price,
                "value": qty * price,
                "pnl": float(pos["walletImpact"]["unrealizedProfitLoss"]),
            }
        )
    return result


def place_market_order(ticker: str, quantity: float) -> dict:
    order = orders.post_place_market_order(quantity, ticker, extended_hours=True)
    return {"id": order.get("id"), "status": order.get("status", "unknown")}


def _normalize_instrument(raw: dict) -> dict:
    ticker = raw.get("ticker", "")
    short = ticker.split("_")[0] if ticker else ""
    return {
        "ticker": ticker,
        "short": short.upper(),
        "name": raw.get("name", ""),
        "type": (raw.get("type") or raw.get("instrumentType") or "").upper(),
        "currency": raw.get("currency", ""),
        "isin": raw.get("isin", ""),
    }


def _load_cache() -> list[dict] | None:
    path = Path(config.INSTRUMENTS_CACHE_PATH)
    if not path.exists():
        return None
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    age_hours = (time.time() - cached.get("fetched_at", 0)) / 3600
    if age_hours > config.INSTRUMENTS_CACHE_HOURS:
        return None
    return cached.get("instruments")


def _save_cache(items: list[dict]) -> None:
    path = Path(config.INSTRUMENTS_CACHE_PATH)
    path.write_text(
        json.dumps({"fetched_at": time.time(), "instruments": items}),
        encoding="utf-8",
    )


def get_instruments(force_refresh: bool = False) -> list[dict]:
    if not force_refresh:
        cached = _load_cache()
        if cached is not None:
            return cached

    raw = instruments.get_available_instruments()
    items = [_normalize_instrument(item) for item in raw if item.get("ticker")]
    _save_cache(items)
    return items


def available_ticker_set(instrument_list: list[dict] | None = None) -> set[str]:
    items = instrument_list if instrument_list is not None else get_instruments()
    return {item["ticker"] for item in items}


def resolve_symbols(symbols: list[str], instrument_list: list[dict]) -> list[dict]:
    """Map short symbols (AAPL, VOO) to Trading212 tickers (AAPL_US_EQ)."""
    by_short: dict[str, list[dict]] = {}
    for item in instrument_list:
        by_short.setdefault(item["short"], []).append(item)

    resolved = []
    seen = set()
    for symbol in symbols:
        key = symbol.strip().upper().split("_")[0]
        candidates = by_short.get(key, [])
        if not candidates:
            # already a full T212 ticker?
            full = next((i for i in instrument_list if i["ticker"] == symbol), None)
            if full and full["ticker"] not in seen:
                resolved.append(full)
                seen.add(full["ticker"])
            continue

        candidates = sorted(
            candidates,
            key=lambda c: (
                0 if c["type"] == "ETF" else 1,
                0 if c["ticker"].endswith("_US_EQ") else 1,
                c["ticker"],
            ),
        )
        pick = candidates[0]
        if pick["ticker"] not in seen:
            resolved.append(pick)
            seen.add(pick["ticker"])

    return resolved
