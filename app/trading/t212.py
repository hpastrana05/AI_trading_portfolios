import json
import time
from pathlib import Path

from app.core import config
from app.trading.api import accounts, instruments, orders, positions


def get_account() -> dict:
    """Account cash/totals. total_value is unset until portfolio_view()."""
    data = accounts.get_account_summary()
    cash = data.get("cash") or {}
    return {
        "currency": data.get("currency", ""),
        "cash_available": float(cash.get("availableToTrade") or 0),
        "cash_in_pies": float(cash.get("inPies") or 0),
        "account_total": float(data.get("totalValue") or 0),
        # Filled by portfolio_view() as investable (excl. pies).
        "total_value": 0.0,
        "pies_excluded_value": 0.0,
    }


def get_positions() -> list[dict]:
    """Tradeable (non-pie) positions only. Fully pie-locked tickers are omitted."""
    data = positions.get_all_open_positions()
    result = []
    for pos in data:
        qty_total = float(pos.get("quantity") or 0)
        if "quantityAvailableForTrading" in pos and pos["quantityAvailableForTrading"] is not None:
            qty_tradeable = float(pos["quantityAvailableForTrading"])
        else:
            qty_tradeable = qty_total
        qty_in_pies = float(pos.get("quantityInPies") or max(0.0, qty_total - qty_tradeable))

        if qty_tradeable <= 1e-9:
            continue

        price = float(pos.get("currentPrice") or 0)
        impact = pos.get("walletImpact") or {}
        total_value = float(impact.get("currentValue", qty_total * price) or 0)
        total_cost = float(impact.get("totalCost") or 0)
        total_pnl = float(impact.get("unrealizedProfitLoss") or 0)

        # Scale wallet impact to the tradeable fraction when part is in pies.
        fraction = (qty_tradeable / qty_total) if qty_total > 0 else 1.0
        value = total_value * fraction if total_value else qty_tradeable * price
        cost = total_cost * fraction
        pnl = total_pnl * fraction
        pnl_pct = (pnl / cost * 100) if cost else 0.0

        result.append(
            {
                "ticker": pos["instrument"]["ticker"],
                "quantity": qty_tradeable,
                "quantity_total": qty_total,
                "quantity_in_pies": qty_in_pies,
                "current_price": price,
                "value": value,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
            }
        )
    return result


def portfolio_view(
    account: dict | None = None,
    positions_list: list[dict] | None = None,
) -> tuple[dict, list[dict]]:
    """Investable portfolio: free cash + tradeable positions (pies excluded)."""
    account = dict(account if account is not None else get_account())
    positions_list = list(positions_list if positions_list is not None else get_positions())

    invested = sum(float(p.get("value") or 0) for p in positions_list)
    cash = float(account.get("cash_available") or 0)
    investable = cash + invested
    account_total = float(account.get("account_total") or 0)
    cash_in_pies = float(account.get("cash_in_pies") or 0)

    account["total_value"] = investable
    account["pies_excluded_value"] = max(0.0, account_total - investable)
    account["cash_in_pies"] = cash_in_pies
    return account, positions_list


def place_market_order(ticker: str, quantity: float) -> dict:
    order = orders.post_place_market_order(quantity, ticker, extended_hours=True)
    return {"id": order.get("id"), "status": order.get("status", "unknown")}


def cancel_all_pending_orders() -> list[dict]:
    """Cancel active equity orders so a reset is not fighting leftover buys/sells."""
    raw = orders.get_pending_orders()
    if raw is None:
        items: list = []
    elif isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        nested = raw.get("items") or raw.get("orders") or raw.get("data") or []
        items = nested if isinstance(nested, list) else []
    else:
        items = []

    results = []
    for item in items:
        if not isinstance(item, dict):
            continue
        oid = item.get("id")
        if oid is None:
            continue
        entry = {"id": oid, "ticker": item.get("ticker"), "ok": True}
        try:
            orders.delete_cancel_pending_order(int(oid))
        except Exception as exc:
            entry["ok"] = False
            entry["error"] = str(exc)
        results.append(entry)
        time.sleep(0.25)
    return results


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
