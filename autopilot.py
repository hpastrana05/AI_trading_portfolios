import json
import threading
import time

import ai
import config
import guardrails
import journal
import memory
import performance
import t212
import timeutil
import usage

_lock = threading.Lock()
_thread: threading.Thread | None = None


RISK_PROMPTS = {
    "low": (
        "Risk: LOW. Be conservative. Prefer ETFs, diversify widely, keep a large cash buffer, "
        "avoid volatile small caps. Prioritize capital preservation."
    ),
    "medium": (
        "Risk: MEDIUM. Balanced growth. Mix ETFs and quality stocks. Moderate position sizes. "
        "Some cash buffer for opportunities."
    ),
    "high": (
        "Risk: HIGH. Aggressive growth. You may concentrate in high-conviction ideas, "
        "use smaller cash buffer, accept higher volatility for higher returns."
    ),
}


def _state_path():
    return config.env_data_dir() / "autopilot_state.json"


def load_state() -> dict:
    path = _state_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {
        "running": False,
        "risk": "medium",
        "last_run": None,
        "last_error": None,
        "last_summary": None,
    }


def save_state(state: dict) -> None:
    _state_path().write_text(json.dumps(state, indent=2), encoding="utf-8")


def risk_prompt(risk: str) -> str:
    return RISK_PROMPTS.get(risk.lower(), RISK_PROMPTS["medium"])


def current_allocation(account: dict, positions: list[dict]) -> dict[str, float]:
    total = account["total_value"]
    if total <= 0:
        return {"CASH": 1.0}
    alloc = {pos["ticker"]: pos["value"] / total for pos in positions}
    alloc["CASH"] = max(0.0, 1.0 - sum(alloc.values()))
    return alloc


def compute_trades(
    target: dict[str, float],
    account: dict,
    positions: list[dict],
    allowed_tickers: set[str],
    estimated_prices: dict[str, float] | None = None,
) -> list[dict]:
    estimated_prices = estimated_prices or {}
    total = account["total_value"]
    price_by_ticker = {p["ticker"]: p["current_price"] for p in positions}
    value_by_ticker = {p["ticker"]: p["value"] for p in positions}

    for ticker, price in estimated_prices.items():
        if ticker not in price_by_ticker and price and float(price) > 0:
            price_by_ticker[ticker] = float(price)

    tickers = (set(target) | set(value_by_ticker)) - {"CASH"}
    trades = []

    for ticker in sorted(tickers):
        if ticker not in allowed_tickers:
            continue
        weight = float(target.get(ticker, 0.0))
        target_value = weight * total
        current_value = value_by_ticker.get(ticker, 0.0)
        diff_value = target_value - current_value
        if abs(diff_value) < 1.0:
            continue
        price = price_by_ticker.get(ticker)
        if not price or price <= 0:
            continue
        quantity = round(diff_value / price, 4)
        if abs(quantity) < 0.0001:
            continue
        trades.append(
            {
                "ticker": ticker,
                "action": "buy" if quantity > 0 else "sell",
                "quantity": quantity,
                "amount": diff_value,
                "price": price,
            }
        )
    return trades


def validate_allocation(allocation: dict[str, float], allowed_tickers: set[str]) -> list[str]:
    errors = []
    total = sum(allocation.values())
    if abs(total - 1.0) > 0.02:
        errors.append(f"Allocation must sum to 1.0 (got {total:.2f})")
    for ticker in allocation:
        if ticker != "CASH" and ticker not in allowed_tickers:
            errors.append(f"Ticker not available on Trading212: {ticker}")
    return errors


def prepare_trades(trades: list[dict], cash_available: float) -> tuple[list[dict], list[str]]:
    """Execute sells first; cap buys to available cash."""
    notes: list[str] = []
    sells = [t for t in trades if t["quantity"] < 0]
    buys = [t for t in trades if t["quantity"] > 0]

    cash_for_buys = max(0.0, cash_available) * 0.95
    buy_cost = sum(t["quantity"] * t["price"] for t in buys)

    if buys and buy_cost > cash_for_buys:
        scale = cash_for_buys / buy_cost if buy_cost > 0 else 0
        scaled = []
        for t in buys:
            qty = round(t["quantity"] * scale, 4)
            if qty < 0.0001:
                continue
            scaled.append({**t, "quantity": qty, "amount": qty * t["price"]})
        buys = scaled
        notes.append(
            f"Buy orders scaled to fit available cash ({cash_available:.2f})"
        )

    return sells + buys, notes


def run_cycle(risk: str) -> dict:
    if timeutil.is_weekend():
        note = "Weekend — trading paused (no AI calls)"
        print(f"[autopilot] {note}", flush=True)
        return {
            "decision": None,
            "executed": [],
            "skipped": [],
            "trades_planned": [],
            "notes": [note],
            "timestamp": timeutil.now_iso(),
        }

    ok_budget, budget_msg = usage.can_afford_cycle()
    if not ok_budget:
        print(f"[autopilot] {budget_msg}", flush=True)
        return {
            "decision": None,
            "executed": [],
            "skipped": [],
            "trades_planned": [],
            "notes": [budget_msg],
            "timestamp": timeutil.now_iso(),
        }

    account = t212.get_account()
    positions = t212.get_positions()
    alloc = current_allocation(account, positions)
    all_instruments = t212.get_instruments()
    available = t212.available_ticker_set(all_instruments)
    mem = memory.load()
    rules = guardrails.load()
    strategy = (
        f"{config.STRATEGY}\n{risk_prompt(risk)}\n"
        f"{guardrails.prompt_text(rules)}\n"
        f"Available cash: {account['cash_available']:.2f} {account['currency']}. "
        "Do not allocate more than available cash. "
        "If the portfolio is already fine, prefer no_changes=true instead of forcing trades."
    )

    def resolve(symbols: list[str]) -> list[dict]:
        return t212.resolve_symbols(symbols, all_instruments)

    decision = ai.get_suggestion(strategy, resolve, alloc, mem, risk)
    ai.log_decision(decision)
    print(f"[autopilot] AI cycle done at {timeutil.now_iso()} risk={risk}", flush=True)

    hold = bool(decision.get("no_changes"))
    target = decision.get("allocation", {})
    estimated = {
        k: float(v)
        for k, v in (decision.get("estimated_prices") or {}).items()
        if k in available
    }

    guard_notes: list[str] = []
    if hold:
        trades, prep_notes = [], ["AI chose to hold — no trades"]
    else:
        errors = validate_allocation(target, available)
        if errors:
            raise RuntimeError("; ".join(errors))
        target, clamp_notes = guardrails.apply_to_allocation(target, rules)
        guard_notes.extend(clamp_notes)
        trades = compute_trades(target, account, positions, available, estimated)
        trades, prep_notes = prepare_trades(trades, account["cash_available"])
        trades, filter_notes = guardrails.filter_trades(
            trades, rules, trades_today=journal.count_trades_today()
        )
        guard_notes.extend(filter_notes)

    reasons = {
        item.get("ticker"): item.get("reason", "")
        for item in decision.get("trade_reasons", [])
    }

    executed = []
    skipped = []
    cash_left = account["cash_available"]
    cycle_id = decision.get("cycle_id")
    cycle_reasoning = decision.get("reasoning", "")
    thinking = decision.get("thinking", "")
    pick_reasoning = decision.get("pick_reasoning", "")

    for trade in trades:
        cost = trade["quantity"] * trade["price"]
        if trade["action"] == "buy" and cost > cash_left * 0.99:
            skipped.append(
                {
                    "ticker": trade["ticker"],
                    "reason": f"Skipped: need {cost:.2f}, only {cash_left:.2f} cash left",
                }
            )
            continue

        try:
            result = t212.place_market_order(trade["ticker"], trade["quantity"])
        except Exception as exc:
            skipped.append({"ticker": trade["ticker"], "reason": str(exc)})
            continue

        if trade["action"] == "buy":
            cash_left -= cost
        else:
            cash_left += abs(cost)

        had_position = trade["ticker"] in {p["ticker"] for p in positions}
        if trade["action"] == "sell" and had_position:
            trade_type = "exit"
        elif trade["action"] == "buy" and not had_position:
            trade_type = "entry"
        else:
            trade_type = "rebalance"

        entry = {
            "type": trade_type,
            "ticker": trade["ticker"],
            "action": trade["action"],
            "quantity": trade["quantity"],
            "price": trade["price"],
            "amount": trade["amount"],
            "reason": reasons.get(trade["ticker"], cycle_reasoning),
            "cycle_id": cycle_id,
            "cycle_reasoning": cycle_reasoning,
            "thinking": thinking,
            "pick_reasoning": pick_reasoning,
            "risk": risk,
            "env": config.T212_ENV,
            "order_id": result.get("id"),
            "status": result.get("status"),
            "where": config.T212_ENV,
        }
        journal.log_trade(entry)
        executed.append({**trade, **result, **entry})

    if decision.get("memory_update") or decision.get("thinking"):
        memory.apply_update(
            decision.get("memory_update", {}),
            thinking=decision.get("thinking", ""),
        )

    try:
        performance.record_snapshot(t212.get_account())
    except Exception:
        pass

    summary_notes = prep_notes + guard_notes
    if skipped:
        summary_notes = summary_notes + [f"Skipped {len(skipped)} trade(s)"]
    if hold and not executed:
        summary_notes = ["Hold — no changes"] + [
            n for n in summary_notes if n != "AI chose to hold — no trades"
        ]

    return {
        "decision": decision,
        "executed": executed,
        "skipped": skipped,
        "trades_planned": trades,
        "notes": summary_notes,
        "timestamp": timeutil.now_iso(),
    }


def _wait_until_next_slot() -> bool:
    """Sleep until the next weekday-aligned clock slot. Returns False if stopped.

    Important: lock the target once. Recalculating next_aligned() each second
    can skip the slot (at 11:00:00.1, 'next' becomes 12:00).
    """
    target = timeutil.next_trading_aligned()
    print(f"[autopilot] waiting until {target.isoformat()}", flush=True)
    while True:
        if not load_state().get("running"):
            return False
        remaining = (target - timeutil.now()).total_seconds()
        if remaining <= 0:
            return True
        time.sleep(min(1.0, remaining))


def _loop() -> None:
    while True:
        if not _wait_until_next_slot():
            print("[autopilot] stopped", flush=True)
            return

        state = load_state()
        if not state.get("running"):
            return
        risk = state.get("risk", "medium")
        print(f"[autopilot] scheduled run starting risk={risk}", flush=True)
        try:
            result = run_cycle(risk)
            state = load_state()
            state["last_run"] = result["timestamp"]
            state["last_error"] = None
            state["last_summary"] = (
                f"Executed {len(result['executed'])} trades"
                if result["executed"]
                else (" · ".join(result.get("notes") or []) or "No trades needed")
            )
            save_state(state)
            print(f"[autopilot] scheduled run done: {state['last_summary']}", flush=True)
        except Exception as exc:
            state = load_state()
            state["last_error"] = str(exc)
            state["last_run"] = timeutil.now_iso()
            save_state(state)
            print(f"[autopilot] scheduled run error: {exc}", flush=True)


def _save_run_result(result: dict | None, exc: Exception | None = None) -> None:
    state = load_state()
    state["last_run"] = timeutil.now_iso()
    if exc:
        state["last_error"] = str(exc)
        save_state(state)
        return
    state["last_error"] = None
    parts = []
    if result:
        n = len(result.get("executed", []))
        if n:
            parts.append(f"Executed {n} trade(s)")
        elif result.get("notes"):
            parts.extend(result["notes"])
        else:
            parts.append("No trades needed")
        if result.get("skipped"):
            parts.append(f"{len(result['skipped'])} skipped")
    state["last_summary"] = " · ".join(parts) if parts else "Done"
    save_state(state)


def start(risk: str | None = None, force_run: bool = True) -> dict:
    global _thread
    with _lock:
        state = load_state()
        if risk:
            state["risk"] = risk.lower()
        state["running"] = True
        save_state(state)
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_loop, daemon=True)
            _thread.start()

    should_run = force_run or timeutil.missed_schedule(state.get("last_run"))
    if should_run:
        reason = "manual start" if force_run else "catch-up after missed slot"
        print(f"[autopilot] immediate run ({reason})", flush=True)
        try:
            result = run_cycle(state["risk"])
            _save_run_result(result)
        except Exception as exc:
            _save_run_result(None, exc)
    else:
        print(
            f"[autopilot] resume without catch-up; next slot {timeutil.next_trading_aligned().isoformat()}",
            flush=True,
        )
    return load_state()


def stop() -> dict:
    state = load_state()
    state["running"] = False
    save_state(state)
    return state


def set_risk(risk: str) -> dict:
    state = load_state()
    state["risk"] = risk.lower()
    save_state(state)
    return state


def run_once(risk: str | None = None) -> dict:
    state = load_state()
    risk = (risk or state.get("risk", "medium")).lower()
    try:
        result = run_cycle(risk)
        _save_run_result(result)
        return result
    except Exception as exc:
        _save_run_result(None, exc)
        return {"error": str(exc), "executed": [], "skipped": []}
