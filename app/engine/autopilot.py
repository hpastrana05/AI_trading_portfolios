import json
import threading
import time

from app.ai import ai, memory, user_guidance
from app.core import config, timeutil
from app.engine import circuit, guardrails
from app.storage import journal, performance, usage
from app.trading import t212

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
    defaults = {
        "running": False,
        "risk": "medium",
        "interval_minutes": int(config.AUTOPILOT_INTERVAL_MINUTES),
        "last_run": None,
        "last_error": None,
        "last_summary": None,
        "protection_mode": "off",
        "protection_override": False,
        "equity_peak": None,
        "drawdown_pct": None,
        "risk_before_protection": None,
        "protection_dd_pct": None,
        "protection_at": None,
    }
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            defaults.update(raw)
        except json.JSONDecodeError:
            pass
    defaults["interval_minutes"] = _normalize_interval(
        defaults.get("interval_minutes", config.AUTOPILOT_INTERVAL_MINUTES)
    )
    return defaults


def save_state(state: dict) -> None:
    _state_path().write_text(json.dumps(state, indent=2), encoding="utf-8")


def _normalize_interval(value) -> int:
    try:
        minutes = int(float(value))
    except (TypeError, ValueError):
        minutes = int(config.AUTOPILOT_INTERVAL_MINUTES)
    return max(5, min(24 * 60, minutes))


def get_interval_minutes(state: dict | None = None) -> int:
    state = state if state is not None else load_state()
    return _normalize_interval(
        state.get("interval_minutes", config.AUTOPILOT_INTERVAL_MINUTES)
    )


def format_interval(minutes) -> str:
    """Human label: minutes under 1h, otherwise hours (e.g. 1h, 1.5h, 24h)."""
    m = _normalize_interval(minutes)
    if m < 60:
        return f"{m} min"
    hours = m / 60
    if hours == int(hours):
        return f"{int(hours)}h"
    return f"{hours:g}h"


def risk_prompt(risk: str) -> str:
    return RISK_PROMPTS.get(risk.lower(), RISK_PROMPTS["medium"])


def _day_start_baseline(account: dict, state: dict) -> tuple[float, str | None]:
    """Portfolio value at the start of today (local app timezone)."""
    today = timeutil.now().date().isoformat()
    if (
        state.get("day_start_date") == today
        and state.get("day_start_value") is not None
        and float(state["day_start_value"]) > 0
    ):
        return float(state["day_start_value"]), state.get("day_start_at")

    start_value = None
    start_at = None

    snap = performance.last_snapshot_before_today()
    if snap and float(snap.get("total_value") or 0) > 0:
        start_value = float(snap["total_value"])
        start_at = snap.get("timestamp")

    if start_value is None:
        prev_at = state.get("last_cycle_at") or state.get("last_run")
        prev_value = state.get("last_cycle_value")
        if prev_value is not None and float(prev_value) > 0 and prev_at:
            prev_dt = timeutil.parse_iso(prev_at)
            if prev_dt and prev_dt.date().isoformat() < today:
                start_value = float(prev_value)
                start_at = prev_at

    if start_value is None:
        start_value = float(account.get("total_value") or 0)
        start_at = timeutil.now_iso()

    state["day_start_value"] = start_value
    state["day_start_date"] = today
    state["day_start_at"] = start_at
    save_state(state)
    return start_value, start_at


def daily_pnl_context(account: dict) -> dict:
    """P&L since local midnight today (portfolio value then → now)."""
    state = load_state()
    now_value = float(account.get("total_value") or 0)
    currency = account.get("currency", "")
    prev_value, prev_at = _day_start_baseline(account, state)

    if prev_value <= 0 or now_value <= 0:
        return {
            "pnl": None,
            "pnl_pct": None,
            "prev_value": None,
            "prev_at": prev_at,
            "now_value": now_value,
            "text": (
                f"No daily baseline yet. Current portfolio value: "
                f"{now_value:.2f} {currency}. "
                "Actively choose the best allocation for the strategy. "
                "Goal: maximize portfolio quality and keep today's P&L positive."
            ),
        }

    pnl = now_value - prev_value
    pnl_pct = pnl / prev_value * 100
    when = timeutil.format_local(prev_at) if prev_at else "start of today"
    if pnl_pct >= 0:
        guidance = (
            "Daily P&L is positive — do NOT default to hold. "
            "Evaluate the best next allocation given this edge: improve the portfolio if a better setup exists, "
            "or keep current weights only if that is truly the best option after comparing alternatives. "
            "Avoid changes that are likely to erase the gain and turn today's P&L negative."
        )
    else:
        guidance = (
            "Daily P&L is negative — seek the best recovery path with controlled risk to turn it positive. "
            "Prefer reallocations that improve expected outcome; avoid revenge trading and avoid deepening losses. "
            "Hold only if staying put is clearly better than available alternatives."
        )
    return {
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "prev_value": prev_value,
        "prev_at": prev_at,
        "now_value": now_value,
        "text": (
            f"Today's P&L (since {when}): {pnl:+.2f} {currency} "
            f"({pnl_pct:+.2f}%). "
            f"Day start {prev_value:.2f} → now {now_value:.2f} {currency}. "
            f"{guidance}"
        ),
    }


def store_cycle_baseline(account: dict) -> None:
    state = load_state()
    state["last_cycle_value"] = float(account.get("total_value") or 0)
    state["last_cycle_at"] = timeutil.now_iso()
    save_state(state)


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
    qty_by_ticker = {p["ticker"]: float(p.get("quantity") or 0) for p in positions}

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
        # Never sell more than shares available outside pies.
        if quantity < 0:
            max_sell = qty_by_ticker.get(ticker, 0.0)
            if max_sell <= 1e-9:
                continue
            if abs(quantity) > max_sell:
                quantity = -round(max_sell, 4)
                diff_value = quantity * price
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


def plan_balanced_withdrawal_sells(
    amount: float,
    account: dict | None = None,
    positions: list[dict] | None = None,
) -> dict:
    """Plan proportional sells so cash covers a withdrawal without skewing weights."""
    amount = float(amount)
    if amount <= 0:
        raise ValueError("Withdrawal amount must be > 0")

    if account is None or positions is None:
        account, positions = t212.portfolio_view()
    else:
        account, positions = t212.portfolio_view(account, positions)

    cash = float(account.get("cash_available") or 0)
    invested = sum(float(p.get("value") or 0) for p in positions)
    currency = account.get("currency", "")

    if cash >= amount - 0.01:
        return {
            "amount": amount,
            "currency": currency,
            "cash_available": cash,
            "cash_shortfall": 0.0,
            "sells": [],
            "expected_proceeds": 0.0,
            "notes": [
                f"Already enough cash ({cash:.2f} {currency}) for {amount:.2f} {currency}. No sells needed."
            ],
        }

    shortfall = amount - cash
    if invested <= 0.01:
        raise ValueError(
            f"Need {shortfall:.2f} more cash but there are no tradeable positions to sell"
        )

    # Sell the same fraction of every position so relative weights stay similar.
    fraction = min(1.0, shortfall / invested)
    sells: list[dict] = []
    expected = 0.0
    notes: list[str] = []

    for pos in sorted(positions, key=lambda p: p["ticker"]):
        value = float(pos.get("value") or 0)
        price = float(pos.get("current_price") or 0)
        qty = float(pos.get("quantity") or 0)
        if value <= 0 or price <= 0 or qty <= 0:
            continue
        target_proceeds = value * fraction
        sell_qty = min(qty, round(target_proceeds / price, 4))
        if sell_qty < 0.0001:
            continue
        # Prefer selling almost-all tiny leftovers when fraction is high.
        if fraction > 0.98 and (qty - sell_qty) * price < 1.0:
            sell_qty = round(qty, 4)
        proceeds = sell_qty * price
        expected += proceeds
        sells.append(
            {
                "ticker": pos["ticker"],
                "action": "sell",
                "quantity": -sell_qty,
                "amount": -proceeds,
                "price": price,
                "weight_before": value / (invested + cash) if (invested + cash) else 0,
            }
        )

    if not sells:
        raise ValueError("Could not build any sell orders for this amount")

    if expected + cash + 0.5 < amount:
        notes.append(
            f"After proportional sells, expected cash ≈ {cash + expected:.2f} "
            f"(target {amount:.2f}). May need a slightly larger amount or full exit."
        )
    else:
        notes.append(
            f"Sell ~{fraction * 100:.1f}% of each position to raise ≈ {expected:.2f} {currency} "
            f"(plus existing cash {cash:.2f})."
        )

    return {
        "amount": amount,
        "currency": currency,
        "cash_available": cash,
        "cash_shortfall": shortfall,
        "sell_fraction": fraction,
        "sells": sells,
        "expected_proceeds": expected,
        "expected_cash_after": cash + expected,
        "notes": notes,
    }


def execute_balanced_withdrawal_sells(amount: float) -> dict:
    """Sell proportionally across holdings to free cash for a manual withdrawal."""
    account, positions = t212.portfolio_view()
    plan = plan_balanced_withdrawal_sells(amount, account, positions)
    executed = []
    skipped = []

    for trade in plan["sells"]:
        try:
            result = t212.place_market_order(trade["ticker"], trade["quantity"])
        except Exception as exc:
            skipped.append({"ticker": trade["ticker"], "reason": str(exc)})
            continue

        entry = {
            "type": "withdrawal_rebalance",
            "ticker": trade["ticker"],
            "action": "sell",
            "quantity": trade["quantity"],
            "price": trade["price"],
            "amount": trade["amount"],
            "reason": f"Balanced sell to free cash for withdrawal of {amount:.2f}",
            "env": config.T212_ENV,
            "where": config.T212_ENV,
            "order_id": result.get("id"),
            "status": result.get("status"),
        }
        journal.log_trade(entry)
        executed.append({**trade, **result, **entry})

    try:
        refreshed, refreshed_positions = t212.portfolio_view()
    except Exception:
        refreshed, refreshed_positions = account, positions

    return {
        "plan": plan,
        "executed": executed,
        "skipped": skipped,
        "account": refreshed,
        "positions": refreshed_positions,
        "notes": list(plan.get("notes") or [])
        + (
            [f"Executed {len(executed)} sell(s)"]
            if executed
            else []
        )
        + ([f"Skipped {len(skipped)} sell(s)"] if skipped else []),
        "timestamp": timeutil.now_iso(),
    }


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

    account, positions = t212.portfolio_view()
    state = load_state()
    state, prot_notes = circuit.evaluate(state, float(account.get("total_value") or 0))
    if prot_notes:
        print(f"[autopilot] protection: {'; '.join(prot_notes)}", flush=True)
    save_state(state)

    if state.get("protection_mode") == "stopped":
        note = prot_notes[0] if prot_notes else "Hard stop active — no trading"
        return {
            "decision": None,
            "executed": [],
            "skipped": [],
            "trades_planned": [],
            "notes": [note],
            "timestamp": timeutil.now_iso(),
        }

    risk = (state.get("risk") or risk or "medium").lower()
    alloc = current_allocation(account, positions)
    all_instruments = t212.get_instruments()
    available = t212.available_ticker_set(all_instruments)
    mem = memory.load()
    base_rules = guardrails.load()
    rules = circuit.safe_rules_overlay(base_rules, state)
    pnl_ctx = daily_pnl_context(account)
    protection_ctx = circuit.prompt_text(state, base_rules)
    strategy = (
        f"{config.STRATEGY}\n{risk_prompt(risk)}\n"
        f"{guardrails.prompt_text(rules)}\n"
        f"{protection_ctx}\n"
        f"{pnl_ctx['text']}\n"
        f"Available cash: {account['cash_available']:.2f} {account['currency']}. "
        "Do not allocate more than available cash. "
        "Portfolio value and holdings exclude Trading212 pies — never manage or assume pie cash/shares. "
        "Current allocation is the ONLY source of truth for what is held. "
        "Each cycle you may pick ANY suitable symbols; the shortlist used for orders this cycle is temporary — "
        "never treat it as a permanent allow-list or hard guardrail in memory. "
        "Temporary order failures are not bans — those tickers remain eligible. "
        "Choose the best option for the portfolio; use no_changes=true only when holding is clearly best after comparing alternatives — never as a default just because daily P&L is green."
    )
    skip_ctx = memory.skips_prompt_text(mem)
    if skip_ctx:
        strategy += "\n" + skip_ctx
    guidance = user_guidance.prompt_text()
    if guidance:
        strategy += "\n" + guidance

    def resolve(symbols: list[str]) -> list[dict]:
        return t212.resolve_symbols(symbols, all_instruments)

    decision = ai.get_suggestion(strategy, resolve, alloc, mem, risk)
    # One-shot: consume user guidance after a successful AI decision.
    if guidance:
        user_guidance.clear()
    decision["daily_pnl"] = {
        "pnl": pnl_ctx["pnl"],
        "pnl_pct": pnl_ctx["pnl_pct"],
        "prev_value": pnl_ctx["prev_value"],
        "now_value": pnl_ctx["now_value"],
        "prev_at": pnl_ctx["prev_at"],
    }
    ai.log_decision(decision)
    print(
        f"[autopilot] AI cycle done at {timeutil.now_iso()} risk={risk} "
        f"daily_pnl_pct={pnl_ctx['pnl_pct']}",
        flush=True,
    )

    hold = bool(decision.get("no_changes"))
    target = decision.get("allocation", {})
    estimated = {
        k: float(v)
        for k, v in (decision.get("estimated_prices") or {}).items()
        if k in available
    }

    guard_notes: list[str] = []
    trades_before_filters: list[dict] = []
    if hold:
        trades, prep_notes = [], ["AI chose to hold — no trades"]
    else:
        errors = validate_allocation(target, available)
        if errors:
            raise RuntimeError("; ".join(errors))
        target, clamp_notes = guardrails.apply_to_allocation(target, rules)
        guard_notes.extend(clamp_notes)
        trades = compute_trades(target, account, positions, available, estimated)
        trades_before_filters = list(trades)
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
    kept_keys = {(t["ticker"], t["action"]) for t in trades}
    for trade in trades_before_filters:
        key = (trade["ticker"], trade["action"])
        if key not in kept_keys:
            skipped.append(
                {
                    "ticker": trade["ticker"],
                    "reason": "Blocked before order (cash limits or guardrails)",
                }
            )

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

    # Refresh actual holdings after fills, then teach memory about skips/fills.
    try:
        refreshed, refreshed_positions = t212.portfolio_view()
        actual_alloc = current_allocation(refreshed, refreshed_positions)
        performance.record_snapshot(refreshed)
        store_cycle_baseline(refreshed)
    except Exception:
        actual_alloc = current_allocation(account, positions)
        store_cycle_baseline(account)

    if skipped or executed:
        memory.record_execution_feedback(executed, skipped, actual_alloc)

    summary_notes = list(prot_notes) + prep_notes + guard_notes
    if pnl_ctx.get("pnl_pct") is not None:
        summary_notes = [f"Today: {pnl_ctx['pnl_pct']:+.2f}%"] + summary_notes
    if state.get("protection_mode") == "safe":
        summary_notes = [f"SAFE MODE active (DD {state.get('drawdown_pct')}%)"] + summary_notes
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

    Recalculates the target if interval_minutes changes while waiting.
    """
    interval = get_interval_minutes()
    target = timeutil.next_trading_aligned(interval_minutes=interval)
    print(
        f"[autopilot] waiting until {target.isoformat()} (every {interval} min)",
        flush=True,
    )
    while True:
        state = load_state()
        if not state.get("running"):
            return False
        current_interval = get_interval_minutes(state)
        if current_interval != interval:
            interval = current_interval
            target = timeutil.next_trading_aligned(interval_minutes=interval)
            print(
                f"[autopilot] interval → {interval} min, waiting until {target.isoformat()}",
                flush=True,
            )
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
        # Starting after a hard stop is an explicit user resume.
        if state.get("protection_mode") in ("safe", "stopped"):
            state = circuit.clear_manual(state, risk or state.get("risk"))
        elif risk:
            state["risk"] = risk.lower()
        state["running"] = True
        state["interval_minutes"] = get_interval_minutes(state)
        save_state(state)
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_loop, daemon=True)
            _thread.start()

    interval = get_interval_minutes(state)
    should_run = force_run or timeutil.missed_schedule(
        state.get("last_run"), interval_minutes=interval
    )
    if should_run:
        reason = "manual start" if force_run else "catch-up after missed slot"
        print(f"[autopilot] immediate run ({reason})", flush=True)
        try:
            result = run_cycle(state["risk"])
            _save_run_result(result)
        except Exception as exc:
            _save_run_result(None, exc)
    else:
        nxt = timeutil.next_trading_aligned(interval_minutes=interval)
        print(f"[autopilot] resume without catch-up; next slot {nxt.isoformat()}", flush=True)
    return load_state()


def stop() -> dict:
    state = load_state()
    state["running"] = False
    save_state(state)
    return state


def set_risk(risk: str) -> dict:
    """Change risk by hand — also clears SAFE / hard-stop lock."""
    state = load_state()
    state = circuit.clear_manual(state, risk)
    save_state(state)
    print(
        f"[autopilot] risk set to {state['risk']} (protection cleared)",
        flush=True,
    )
    return state


def clear_protection(risk: str | None = None) -> dict:
    state = load_state()
    state = circuit.clear_manual(state, risk or state.get("risk"))
    save_state(state)
    return state


def refresh_protection(equity: float) -> dict:
    """Update peak / drawdown / mode without running a cycle."""
    state = load_state()
    state, notes = circuit.evaluate(state, float(equity or 0))
    save_state(state)
    if notes:
        print(f"[autopilot] protection: {'; '.join(notes)}", flush=True)
    return state


def set_interval(minutes) -> dict:
    state = load_state()
    state["interval_minutes"] = _normalize_interval(minutes)
    save_state(state)
    print(
        f"[autopilot] interval set to {state['interval_minutes']} min "
        f"(running={bool(state.get('running'))})",
        flush=True,
    )
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
