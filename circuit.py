"""Drawdown circuit breaker: optional safe mode + hard stop."""

from __future__ import annotations

import guardrails
import timeutil


def current_drawdown_pct(equity: float, peak: float | None) -> float:
    equity = float(equity or 0)
    peak = float(peak or 0)
    if peak <= 0 or equity < 0:
        return 0.0
    if equity >= peak:
        return 0.0
    return (peak - equity) / peak * 100.0


def update_peak(state: dict, equity: float) -> dict:
    equity = float(equity or 0)
    peak = state.get("equity_peak")
    if peak is None or equity > float(peak):
        state["equity_peak"] = equity
    return state


def evaluate(state: dict, equity: float, rules: dict | None = None) -> tuple[dict, list[str]]:
    """Update protection_mode from drawdown vs optional thresholds.

    Returns (state, notes).
    - safe_dd_pct / stop_dd_pct = None → that level is disabled
    - Manual risk changes should call clear_manual() so the user can raise risk again
    - After manual clear, protection_override stays on until DD recovers (hysteresis)
    """
    rules = rules or guardrails.load()
    notes: list[str] = []
    state = update_peak(state, equity)
    peak = float(state.get("equity_peak") or 0)
    dd = current_drawdown_pct(equity, peak)
    state["drawdown_pct"] = round(dd, 2)

    safe_dd = rules.get("safe_dd_pct")
    stop_dd = rules.get("stop_dd_pct")
    mode = state.get("protection_mode") or "off"
    override = bool(state.get("protection_override"))

    # Clear override once DD has eased enough that protection can re-arm.
    recover_below = None
    if safe_dd is not None:
        recover_below = float(safe_dd) * 0.7
    elif stop_dd is not None:
        recover_below = float(stop_dd) * 0.7
    if override and recover_below is not None and dd < recover_below:
        state["protection_override"] = False
        override = False
        notes.append(f"Protection re-armed: drawdown eased to {dd:.1f}%")
    elif override and safe_dd is None and stop_dd is None:
        state["protection_override"] = False
        override = False

    if override:
        # User unlocked by hand — track DD but do not force safe/stop.
        if mode in ("safe", "stopped"):
            state["protection_mode"] = "off"
        return state, notes

    # Hard stop wins.
    if stop_dd is not None and dd >= float(stop_dd):
        if mode != "stopped":
            state["risk_before_protection"] = state.get("risk", "medium")
            notes.append(
                f"HARD STOP: drawdown {dd:.1f}% ≥ {float(stop_dd):.1f}% from peak — autopilot stopped"
            )
        state["protection_mode"] = "stopped"
        state["running"] = False
        state["risk"] = "low"
        state["protection_dd_pct"] = round(dd, 2)
        state["protection_at"] = timeutil.now_iso()
        return state, notes

    # Safe mode.
    if safe_dd is not None and dd >= float(safe_dd):
        if mode != "safe":
            if mode != "stopped":
                state["risk_before_protection"] = state.get("risk", "medium")
            notes.append(
                f"SAFE MODE: drawdown {dd:.1f}% ≥ {float(safe_dd):.1f}% from peak — risk forced to low"
            )
        state["protection_mode"] = "safe"
        state["risk"] = "low"
        state["protection_dd_pct"] = round(dd, 2)
        state["protection_at"] = timeutil.now_iso()
        return state, notes

    # User disabled safe threshold while already in safe → clear.
    if mode == "safe" and safe_dd is None:
        prev = state.get("risk_before_protection") or "medium"
        state["protection_mode"] = "off"
        state["risk"] = prev
        state["protection_dd_pct"] = round(dd, 2)
        notes.append("SAFE MODE cleared: safe_dd_pct disabled — risk restored")
        return state, notes

    # Recovery: leave safe mode automatically when DD eases (with hysteresis).
    if mode == "safe" and safe_dd is not None and dd < float(safe_dd) * 0.7:
        prev = state.get("risk_before_protection") or "medium"
        state["protection_mode"] = "off"
        state["risk"] = prev
        state["protection_dd_pct"] = round(dd, 2)
        notes.append(
            f"SAFE MODE cleared: drawdown eased to {dd:.1f}% — risk restored to {prev}"
        )
        return state, notes

    if mode == "stopped":
        # Stay stopped until user clears manually (risk change / start).
        return state, notes

    state["protection_mode"] = "off"
    return state, notes


def clear_manual(state: dict, risk: str | None = None) -> dict:
    """User overrides protection (e.g. changing risk by hand)."""
    state["protection_mode"] = "off"
    state["protection_override"] = True
    state["protection_cleared_at"] = timeutil.now_iso()
    if risk:
        state["risk"] = risk.lower()
    return state


def safe_rules_overlay(rules: dict | None = None, state: dict | None = None) -> dict:
    """While in safe mode, tighten min cash if configured."""
    rules = dict(rules or guardrails.load())
    state = state or {}
    if state.get("protection_mode") != "safe":
        return rules
    safe_cash = rules.get("safe_min_cash_pct")
    if safe_cash is None:
        safe_cash = 50.0
    current = rules.get("min_cash_pct")
    if current is None or float(current) < float(safe_cash):
        rules["min_cash_pct"] = float(safe_cash)
    return rules


def prompt_text(state: dict | None = None, rules: dict | None = None) -> str:
    state = state or {}
    rules = rules or guardrails.load()
    mode = state.get("protection_mode") or "off"
    dd = state.get("drawdown_pct")
    lines = []
    if mode == "safe":
        lines.append(
            "PROTECTION: SAFE MODE active due to drawdown. "
            "Trade defensively, prefer cash, risk is LOW. Do not add aggressive risk."
        )
    elif mode == "stopped":
        lines.append(
            "PROTECTION: HARD STOP active due to drawdown. No new risk-taking."
        )
    if dd is not None:
        lines.append(f"Current drawdown from equity peak: {float(dd):.2f}%.")
    safe_dd = rules.get("safe_dd_pct")
    stop_dd = rules.get("stop_dd_pct")
    if safe_dd is not None:
        lines.append(f"Safe-mode threshold: {float(safe_dd):.1f}% DD.")
    if stop_dd is not None:
        lines.append(f"Hard-stop threshold: {float(stop_dd):.1f}% DD.")
    return "\n".join(lines)
