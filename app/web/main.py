import json
import time
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.ai import memory, user_guidance
from app.core import config, timeutil
from app.engine import autopilot, guardrails
from app.storage import journal, performance, usage
from app.trading import t212

BASE_DIR = config.BASE_DIR

_ACCOUNT_CACHE: dict[str, object] = {"ts": 0.0, "data": None}
_POSITIONS_CACHE: dict[str, object] = {"ts": 0.0, "data": None}


def _get_portfolio_cached() -> tuple[dict, list[dict]]:
    """Investable account + tradeable positions (pies excluded)."""
    now = time.time()
    account_stale = _ACCOUNT_CACHE["data"] is None or now - float(_ACCOUNT_CACHE["ts"]) > 5
    positions_stale = _POSITIONS_CACHE["data"] is None or now - float(_POSITIONS_CACHE["ts"]) > 1
    if account_stale or positions_stale:
        account, positions = t212.portfolio_view()
        _ACCOUNT_CACHE["data"] = account
        _ACCOUNT_CACHE["ts"] = now
        _POSITIONS_CACHE["data"] = positions
        _POSITIONS_CACHE["ts"] = now
    return (
        _ACCOUNT_CACHE["data"],  # type: ignore[return-value]
        _POSITIONS_CACHE["data"],  # type: ignore[return-value]
    )


def _get_account_cached() -> dict:
    account, _ = _get_portfolio_cached()
    return account


def _get_positions_cached() -> list[dict]:
    _, positions = _get_portfolio_cached()
    return positions


@asynccontextmanager
async def lifespan(app: FastAPI):
    state = autopilot.load_state()
    if state.get("running"):
        autopilot.start(state.get("risk", "medium"), force_run=False)
    yield


app = FastAPI(title="AI Trading Autopilot", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.filters["local_time"] = timeutil.format_local
templates.env.globals["other_env_url"] = config.OTHER_ENV_URL
templates.env.globals["other_env_port"] = config.OTHER_ENV_PORT
templates.env.globals["other_env"] = config.OTHER_ENV


def _market_status() -> dict:
    return timeutil.equity_market_status()


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    error = None
    account = None
    positions = []
    alloc = {}
    state = autopilot.load_state()

    try:
        account = _get_account_cached()
        performance.record_snapshot(account)
        positions = _get_positions_cached()
        alloc = autopilot.current_allocation(account, positions)
        state = autopilot.refresh_protection(float(account.get("total_value") or 0))
    except Exception as exc:
        error = str(exc)

    next_run = None
    interval = autopilot.get_interval_minutes(state)
    if state.get("running"):
        next_run = timeutil.next_trading_aligned(interval_minutes=interval).isoformat()

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "active": "dashboard",
            "account": account,
            "positions": positions,
            "allocation": alloc,
            "strategy": config.STRATEGY,
            "env": config.T212_ENV,
            "state": state,
            "interval": interval,
            "interval_label": autopilot.format_interval(interval),
            "interval_options": [
                {"minutes": m, "label": autopilot.format_interval(m)}
                for m in (15, 30, 45, 60, 90, 120, 180, 240, 360, 720, 1440)
            ],
            "interval_option_minutes": [15, 30, 45, 60, 90, 120, 180, 240, 360, 720, 1440],
            "next_run": next_run,
            "weekend": timeutil.is_weekend(),
            "error": error,
            "guidance": user_guidance.load(),
            "protection_rules": guardrails.load(),
        },
    )


@app.post("/start")
def start_autopilot(risk: str = Form("medium")):
    autopilot.start(risk, force_run=True)
    return RedirectResponse(url="/", status_code=303)


@app.post("/stop")
def stop_autopilot():
    autopilot.stop()
    return RedirectResponse(url="/", status_code=303)


@app.post("/risk")
def set_risk(risk: str = Form("medium")):
    autopilot.set_risk(risk)
    return RedirectResponse(url="/", status_code=303)


@app.post("/protection/clear")
def clear_protection(risk: str = Form(None)):
    autopilot.clear_protection(risk)
    return RedirectResponse(url="/", status_code=303)


@app.post("/interval")
def set_interval(interval_minutes: str = Form("60")):
    autopilot.set_interval(interval_minutes)
    return RedirectResponse(url="/", status_code=303)


@app.post("/run-once")
def run_once_endpoint(risk: str = Form(None)):
    try:
        autopilot.run_once(risk)
    except Exception:
        pass
    return RedirectResponse(url="/", status_code=303)


def _reset_redirect(next_url: str, result: dict) -> RedirectResponse:
    dest = next_url if next_url in ("/settings", "/", "/performance") else "/settings"
    sold = len(result.get("executed") or [])
    skipped = len(result.get("skipped") or [])
    params = f"reset=1&sold={sold}&skipped={skipped}"
    err = result.get("error")
    if err:
        params += f"&reset_error={quote(str(err)[:240], safe='')}"
    sep = "&" if "?" in dest else "?"
    return RedirectResponse(url=f"{dest}{sep}{params}", status_code=303)


@app.post("/reset")
def reset_portfolio_endpoint(next: str = Form("/settings")):
    result = autopilot.reset_portfolio()
    _ACCOUNT_CACHE["data"] = None
    _POSITIONS_CACHE["data"] = None
    return _reset_redirect(next, result)


@app.get("/history", response_class=HTMLResponse)
def history(request: Request):
    return templates.TemplateResponse(
        request,
        "history.html",
        {"active": "history", "trades": journal.list_trades(), "env": config.T212_ENV},
    )


def _memory_error_redirect(message: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"/memory?error={quote(str(message)[:240], safe='')}",
        status_code=303,
    )


@app.get("/memory", response_class=HTMLResponse)
def memory_page(
    request: Request,
    cleared: int = 0,
    tracking: str | None = None,
    copied: int = 0,
    imported: int = 0,
    wiped: int = 0,
    error: str | None = None,
):
    return templates.TemplateResponse(
        request,
        "memory.html",
        {
            "active": "memory",
            "memory": memory.load(),
            "env": config.T212_ENV,
            "can_copy_demo": config.T212_ENV != "DEMO",
            "cleared": bool(cleared),
            "tracking_saved": tracking,
            "copied": bool(copied),
            "imported": bool(imported),
            "wiped": bool(wiped),
            "error": error,
        },
    )


@app.post("/memory/unlock-tickers")
def memory_unlock_tickers():
    memory.clear_ticker_scars()
    return RedirectResponse(url="/memory?cleared=1", status_code=303)


@app.post("/memory/clear")
def memory_clear_all():
    memory.clear_all()
    return RedirectResponse(url="/memory?wiped=1", status_code=303)


@app.post("/memory/skip-tracking")
def memory_skip_tracking(enabled: str = Form("0")):
    on = str(enabled).strip().lower() in {"1", "true", "on", "yes"}
    memory.set_skip_tracking(on)
    return RedirectResponse(
        url=f"/memory?tracking={'on' if on else 'off'}",
        status_code=303,
    )


@app.post("/memory/copy-from-demo")
def memory_copy_from_demo():
    if config.T212_ENV == "DEMO":
        return _memory_error_redirect("Already on DEMO — open LIVE to copy from DEMO.")
    try:
        memory.copy_from_env("DEMO")
    except FileNotFoundError:
        return _memory_error_redirect("No DEMO memory file found yet.")
    except ValueError as exc:
        return _memory_error_redirect(str(exc))
    return RedirectResponse(url="/memory?copied=1", status_code=303)


@app.get("/memory/export")
def memory_export():
    payload = memory.export_payload()
    stamp = timeutil.now().strftime("%Y%m%d-%H%M")
    filename = f"thinking-{config.T212_ENV.lower()}-{stamp}.json"
    body = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    return Response(
        content=body.encode("utf-8"),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


_MAX_THINKING_IMPORT_BYTES = 512_000


@app.post("/memory/import")
async def memory_import(file: UploadFile = File(...)):
    raw = await file.read()
    if not raw:
        return _memory_error_redirect("The uploaded file was empty.")
    if len(raw) > _MAX_THINKING_IMPORT_BYTES:
        return _memory_error_redirect("Thinking file is too large.")
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _memory_error_redirect("Not valid JSON. Export thinking and upload that file.")
    try:
        memory.import_payload(payload)
    except ValueError as exc:
        return _memory_error_redirect(str(exc))
    return RedirectResponse(url="/memory?imported=1", status_code=303)


@app.get("/performance", response_class=HTMLResponse)
def performance_page(
    request: Request,
    deposit_saved: int = 0,
    withdrawal_saved: int = 0,
    sell_done: int = 0,
    liquidate_done: int = 0,
    liquidate_error: str | None = None,
):
    capital = performance.load_capital()
    try:
        account = _get_account_cached()
        performance.record_snapshot(account)
    except Exception:
        pass
    return templates.TemplateResponse(
        request,
        "performance.html",
        {
            "active": "performance",
            "env": config.T212_ENV,
            "can_deposit": config.T212_ENV == "LIVE",
            "demo_baseline": capital.get("baseline")
            if config.T212_ENV == "DEMO"
            else config.DEMO_BASELINE,
            "demo_start_date": capital.get("start_date") or config.DEMO_START_DATE,
            "deposit_saved": bool(deposit_saved),
            "withdrawal_saved": bool(withdrawal_saved),
            "sell_done": bool(sell_done),
            "error": None,
            "sell_result": None,
            "market": _market_status(),
            "liquidate_done": bool(liquidate_done),
            "liquidate_result": None,
            "liquidate_error": liquidate_error,
            "liquidate_sold": 0,
            "liquidate_skipped": 0,
        },
    )


@app.get("/api/performance")
def performance_api(range: str = "max"):
    data = performance.build_performance(range)
    return JSONResponse(data)


@app.post("/performance/reset")
def performance_reset():
    result = autopilot.reset_portfolio()
    _ACCOUNT_CACHE["data"] = None
    _POSITIONS_CACHE["data"] = None
    return _reset_redirect("/settings", result)


@app.post("/performance/deposit")
def performance_deposit(amount: str = Form("")):
    if config.T212_ENV != "LIVE":
        return RedirectResponse(url="/performance", status_code=303)
    try:
        performance.add_deposit(float(amount), reason="manual")
    except Exception:
        return RedirectResponse(url="/performance", status_code=303)
    return RedirectResponse(url="/performance?deposit_saved=1", status_code=303)


@app.post("/performance/withdrawal")
def performance_withdrawal(amount: str = Form("")):
    if config.T212_ENV != "LIVE":
        return RedirectResponse(url="/performance", status_code=303)
    try:
        performance.add_withdrawal(float(amount), reason="manual")
    except Exception:
        return RedirectResponse(url="/performance", status_code=303)
    return RedirectResponse(url="/performance?withdrawal_saved=1", status_code=303)


@app.post("/performance/sell-for-withdrawal")
def performance_sell_for_withdrawal(request: Request, amount: str = Form("")):
    if config.T212_ENV != "LIVE":
        return RedirectResponse(url="/performance", status_code=303)

    def _page(status_code: int = 200, **extra):
        ctx = {
            "active": "performance",
            "env": config.T212_ENV,
            "can_deposit": True,
            "demo_baseline": config.DEMO_BASELINE,
            "demo_start_date": config.DEMO_START_DATE,
            "deposit_saved": False,
            "withdrawal_saved": False,
            "sell_done": False,
            "error": None,
            "sell_result": None,
            "market": _market_status(),
            "liquidate_done": False,
            "liquidate_result": None,
            "liquidate_error": None,
            "liquidate_sold": 0,
            "liquidate_skipped": 0,
        }
        ctx.update(extra)
        return templates.TemplateResponse(
            request, "performance.html", ctx, status_code=status_code
        )

    try:
        result = autopilot.execute_balanced_withdrawal_sells(float(amount))
    except Exception as exc:
        return _page(status_code=400, error=str(exc))

    _ACCOUNT_CACHE["data"] = None
    _POSITIONS_CACHE["data"] = None
    return _page(sell_done=True, sell_result=result)


@app.post("/performance/sell-all")
def performance_sell_all(request: Request):
    capital = performance.load_capital()

    def _page(status_code: int = 200, **extra):
        ctx = {
            "active": "performance",
            "env": config.T212_ENV,
            "can_deposit": config.T212_ENV == "LIVE",
            "demo_baseline": capital.get("baseline")
            if config.T212_ENV == "DEMO"
            else config.DEMO_BASELINE,
            "demo_start_date": capital.get("start_date") or config.DEMO_START_DATE,
            "deposit_saved": False,
            "withdrawal_saved": False,
            "sell_done": False,
            "error": None,
            "sell_result": None,
            "market": _market_status(),
            "liquidate_done": False,
            "liquidate_result": None,
            "liquidate_error": None,
            "liquidate_sold": 0,
            "liquidate_skipped": 0,
        }
        ctx.update(extra)
        return templates.TemplateResponse(
            request, "performance.html", ctx, status_code=status_code
        )

    try:
        result = autopilot.sell_all_holdings()
    except Exception as exc:
        return _page(status_code=400, liquidate_error=str(exc))

    _ACCOUNT_CACHE["data"] = None
    _POSITIONS_CACHE["data"] = None
    return _page(
        liquidate_done=True,
        liquidate_result=result,
        liquidate_sold=len(result.get("executed") or []),
        liquidate_skipped=len(result.get("skipped") or []),
    )


@app.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    saved: int = 0,
    guidance_saved: int = 0,
    guidance_cleared: int = 0,
    error: str | None = None,
    reset: int = 0,
    sold: int = 0,
    skipped: int = 0,
    reset_error: str | None = None,
):
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "active": "settings",
            "env": config.T212_ENV,
            "rules": guardrails.load(),
            "guidance": user_guidance.load(),
            "saved": bool(saved),
            "guidance_saved": bool(guidance_saved),
            "guidance_cleared": bool(guidance_cleared),
            "error": error,
            "market": _market_status(),
            "reset_done": bool(reset),
            "reset_sold": int(sold or 0),
            "reset_skipped": int(skipped or 0),
            "reset_error": reset_error,
        },
    )


@app.post("/settings")
def settings_save(
    request: Request,
    max_position_pct: str = Form(""),
    min_cash_pct: str = Form(""),
    max_trades_per_day: str = Form(""),
    max_order_amount: str = Form(""),
    safe_dd_pct: str = Form(""),
    stop_dd_pct: str = Form(""),
    safe_min_cash_pct: str = Form(""),
):
    try:
        guardrails.save(
            {
                "max_position_pct": max_position_pct,
                "min_cash_pct": min_cash_pct,
                "max_trades_per_day": max_trades_per_day,
                "max_order_amount": max_order_amount,
                "safe_dd_pct": safe_dd_pct,
                "stop_dd_pct": stop_dd_pct,
                "safe_min_cash_pct": safe_min_cash_pct,
            }
        )
        return RedirectResponse(url="/settings?saved=1", status_code=303)
    except Exception as exc:
        return templates.TemplateResponse(
            request,
            "settings.html",
            {
                "active": "settings",
                "env": config.T212_ENV,
                "rules": {
                    "max_position_pct": max_position_pct or None,
                    "min_cash_pct": min_cash_pct or None,
                    "max_trades_per_day": max_trades_per_day or None,
                    "max_order_amount": max_order_amount or None,
                    "safe_dd_pct": safe_dd_pct or None,
                    "stop_dd_pct": stop_dd_pct or None,
                    "safe_min_cash_pct": safe_min_cash_pct or None,
                },
                "guidance": user_guidance.load(),
                "saved": False,
                "guidance_saved": False,
                "guidance_cleared": False,
                "error": str(exc),
                "market": _market_status(),
                "reset_done": False,
                "reset_sold": 0,
                "reset_skipped": 0,
                "reset_error": None,
            },
            status_code=400,
        )


@app.post("/guidance")
def guidance_save(text: str = Form("")):
    user_guidance.save(text)
    return RedirectResponse(url="/settings?guidance_saved=1", status_code=303)


@app.post("/guidance/clear")
def guidance_clear():
    user_guidance.clear()
    return RedirectResponse(url="/settings?guidance_cleared=1", status_code=303)


@app.get("/usage", response_class=HTMLResponse)
def usage_page(request: Request):
    return templates.TemplateResponse(
        request,
        "usage.html",
        {
            "active": "usage",
            "env": config.T212_ENV,
            "usage": usage.summary(),
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=config.APP_HOST, port=config.APP_PORT, reload=True)
