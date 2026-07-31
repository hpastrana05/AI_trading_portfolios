import json
import re
import uuid
from pathlib import Path

from google import genai

import config
import guardrails
import timeutil
import usage

_client_instance: genai.Client | None = None
_active_model: str | None = None


def _client() -> genai.Client:
    global _client_instance
    if _client_instance is None:
        if not config.GEMINI_API_KEY:
            raise RuntimeError("Missing GEMINI_API_KEY in .env")
        _client_instance = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client_instance


def _model_candidates() -> list[str]:
    if config.GEMINI_MODEL.lower() != "auto":
        return [config.GEMINI_MODEL]
    return config.GEMINI_MODEL_FALLBACKS


def _usage_from_response(response) -> tuple[int, int]:
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return 0, 0
    inp = int(getattr(meta, "prompt_token_count", 0) or 0)
    out = int(getattr(meta, "candidates_token_count", 0) or 0)
    if not out:
        out = int(getattr(meta, "output_token_count", 0) or 0)
    return inp, out


def _generate(prompt: str, purpose: str = "unknown"):
    global _active_model
    errors: list[str] = []

    if _active_model:
        try:
            response = _client().models.generate_content(
                model=_active_model,
                contents=prompt,
            )
            inp, out = _usage_from_response(response)
            usage.log_call(model=_active_model, purpose=purpose, input_tokens=inp, output_tokens=out)
            return response
        except Exception as exc:
            usage.log_call(
                model=_active_model or "unknown",
                purpose=purpose,
                ok=False,
                error=str(exc),
            )
            _active_model = None

    for model in _model_candidates():
        try:
            response = _client().models.generate_content(model=model, contents=prompt)
            _active_model = model
            inp, out = _usage_from_response(response)
            usage.log_call(model=model, purpose=purpose, input_tokens=inp, output_tokens=out)
            return response
        except Exception as exc:
            usage.log_call(model=model, purpose=purpose, ok=False, error=str(exc))
            errors.append(f"{model}: {exc}")

    raise RuntimeError("No Gemini model available. Tried: " + "; ".join(errors))


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return json.loads(text)


def gemini_pick_symbols(
    strategy: str,
    current_allocation: dict[str, float] | None = None,
    memory: dict | None = None,
    max_picks: int | None = None,
) -> dict:
    max_picks = max_picks or config.MAX_PICKS
    holdings = ""
    if current_allocation:
        holdings = f"\nCurrent holdings: {json.dumps(current_allocation)}"
    mem = ""
    if memory:
        mem = (
            f"\nPortfolio memory:\n"
            f"Thesis: {memory.get('portfolio_thesis', '')}\n"
            f"Plan: {memory.get('management_plan', '')}\n"
            f"Notes: {memory.get('notes', '')}"
        )

    prompt = f"""You manage a Trading212 portfolio autonomously.
{strategy}
Pick up to {max_picks} stock/ETF symbols (short names only, e.g. AAPL, VOO — NOT exchange suffixes).
You decide position count and sizing later — pick the best universe for this strategy.
If the current holdings already fit the strategy, you may keep the same symbols.{holdings}{mem}

Respond with ONLY valid JSON:
{{"symbols": ["VOO", "QQQ"], "pick_reasoning": "..."}}"""

    response = _generate(prompt, purpose="pick_symbols")
    data = _parse_json(response.text)
    symbols = [str(s).strip().upper() for s in data.get("symbols", []) if str(s).strip()]
    return {
        "symbols": symbols[:max_picks],
        "pick_reasoning": data.get("pick_reasoning", ""),
    }


def gemini_research(instruments: list[dict], strategy: str, memory: dict | None = None) -> str:
    lines = [f"{i['ticker']} ({i['short']}, {i['name']}, {i['type']})" for i in instruments]
    mem = ""
    if memory:
        mem = f"\nCurrent management plan: {memory.get('management_plan', '')}"
    prompt = (
        f"Research these Trading212 instruments. {strategy}\n"
        + "\n".join(f"- {line}" for line in lines)
        + f"{mem}\n"
        "For each: context, sentiment, risks. Be concise. Plain text only."
    )
    response = _generate(prompt, purpose="research")
    return response.text


def gemini_decide(
    instruments: list[dict],
    research: str,
    strategy: str,
    current_allocation: dict[str, float] | None = None,
    memory: dict | None = None,
    risk: str = "medium",
) -> dict:
    allowed = [i["ticker"] for i in instruments]
    catalog = ", ".join(f"{i['ticker']} ({i['name']}, {i['type']})" for i in instruments)
    allocation_hint = ""
    if current_allocation:
        allocation_hint = f"\nCurrent allocation: {json.dumps(current_allocation)}"
    mem = ""
    if memory:
        mem = (
            f"\nPortfolio memory (you may update this):\n"
            f"Thesis: {memory.get('portfolio_thesis', '')}\n"
            f"Plan: {memory.get('management_plan', '')}\n"
            f"Lessons: {json.dumps(memory.get('lessons', []))}\n"
            f"Notes: {memory.get('notes', '')}"
        )
    rules_text = guardrails.prompt_text()

    prompt = f"""You fully manage this Trading212 portfolio. Risk level: {risk.upper()}.
{strategy}
{rules_text}
You control allocation, position sizes, cash level, entries and exits.
Only use these exact Trading212 tickers plus CASH:
{catalog}
Allowed: {", ".join(allowed)} and CASH.
Weights must sum to 1.0.
Include estimated_prices for tickers not currently held.
IMPORTANT about period P&L: use "Since last cycle" P&L % as context for the decision.
Your job is to pick the best allocation given that P&L — improve when better options exist.
Avoid decisions likely to turn a positive period P&L negative, or to deepen a negative period P&L.
Do NOT choose hold/no_changes merely because period P&L is positive.
Set "no_changes": true ONLY if, after comparing alternatives, holding is truly the best option.
Explain that comparison in thinking/reasoning.{allocation_hint}{mem}

Research:
{research}

Respond with ONLY valid JSON:
{{
  "no_changes": false,
  "allocation": {{"TICKER_US_EQ": 0.XX, "CASH": 0.XX}},
  "estimated_prices": {{"TICKER_US_EQ": 123.45}},
  "reasoning": "overall decision summary",
  "thinking": "detailed thinking about portfolio management",
  "trade_reasons": [{{"ticker": "TICKER_US_EQ", "action": "buy|sell|hold", "reason": "why"}}],
  "memory_update": {{
    "portfolio_thesis": "updated thesis",
    "management_plan": "how you will manage going forward",
    "lessons": ["optional new lesson"],
    "notes": "anything important to remember"
  }}
}}"""

    response = _generate(prompt, purpose="decide")
    return _parse_json(response.text)


def get_suggestion(
    strategy: str,
    resolve_symbols_fn,
    current_allocation: dict[str, float] | None = None,
    memory: dict | None = None,
    risk: str = "medium",
) -> dict:
    picks = gemini_pick_symbols(strategy, current_allocation, memory)
    resolved = resolve_symbols_fn(picks["symbols"])
    if not resolved:
        raise RuntimeError(
            "None of the AI-picked symbols are available on Trading212: "
            + ", ".join(picks["symbols"])
        )

    research = gemini_research(resolved, strategy, memory)
    decision = gemini_decide(
        resolved, research, strategy, current_allocation, memory, risk
    )
    decision["research"] = research
    decision["picked_symbols"] = picks["symbols"]
    decision["pick_reasoning"] = picks["pick_reasoning"]
    decision["risk"] = risk
    decision["resolved_instruments"] = [
        {"ticker": i["ticker"], "name": i["name"], "type": i["type"]}
        for i in resolved
    ]
    decision["cycle_id"] = str(uuid.uuid4())
    decision["timestamp"] = timeutil.now_iso()
    decision["env"] = config.T212_ENV
    return decision


def log_decision(decision: dict, path: str | None = None) -> None:
    log_path = Path(path) if path else config.env_data_dir() / "decisions.jsonl"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(decision) + "\n")
