import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import autopilot
import config
import guardrails
import journal
import memory
import performance
import t212
import timeutil
import usage
import user_guidance

BASE_DIR = Path(__file__).resolve().parent

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
    except Exception as exc:
        error = str(exc)

    next_run = None
    if state.get("running"):
        next_run = timeutil.next_trading_aligned().isoformat()

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
            "interval": config.AUTOPILOT_INTERVAL_MINUTES,
            "next_run": next_run,
            "weekend": timeutil.is_weekend(),
            "error": error,
            "guidance": user_guidance.load(),
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


@app.post("/run-once")
def run_once_endpoint(risk: str = Form(None)):
    try:
        autopilot.run_once(risk)
    except Exception:
        pass
    return RedirectResponse(url="/", status_code=303)


@app.get("/history", response_class=HTMLResponse)
def history(request: Request):
    return templates.TemplateResponse(
        request,
        "history.html",
        {"active": "history", "trades": journal.list_trades(), "env": config.T212_ENV},
    )


@app.get("/memory", response_class=HTMLResponse)
def memory_page(request: Request):
    return templates.TemplateResponse(
        request,
        "memory.html",
        {"active": "memory", "memory": memory.load(), "env": config.T212_ENV},
    )


@app.get("/performance", response_class=HTMLResponse)
def performance_page(request: Request, reset: int = 0, deposit_saved: int = 0):
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
            "can_reset": config.T212_ENV == "DEMO",
            "can_deposit": config.T212_ENV == "LIVE",
            "demo_baseline": config.DEMO_BASELINE,
            "reset_done": bool(reset),
            "deposit_saved": bool(deposit_saved),
        },
    )


@app.get("/api/performance")
def performance_api(range: str = "max"):
    data = performance.build_performance(range)
    return JSONResponse(data)


@app.post("/performance/reset")
def performance_reset():
    if config.T212_ENV != "DEMO":
        return RedirectResponse(url="/performance", status_code=303)
    try:
        autopilot.stop()
    except Exception:
        pass
    performance.reset_demo_local_data()
    # Force portfolio cache refresh after wipe.
    _ACCOUNT_CACHE["data"] = None
    _POSITIONS_CACHE["data"] = None
    return RedirectResponse(url="/performance?reset=1", status_code=303)


@app.post("/performance/deposit")
def performance_deposit(amount: str = Form("")):
    if config.T212_ENV != "LIVE":
        return RedirectResponse(url="/performance", status_code=303)
    try:
        performance.add_deposit(float(amount), reason="manual")
    except Exception:
        return RedirectResponse(url="/performance", status_code=303)
    return RedirectResponse(url="/performance?deposit_saved=1", status_code=303)


@app.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    saved: int = 0,
    guidance_saved: int = 0,
    guidance_cleared: int = 0,
    error: str | None = None,
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
        },
    )


@app.post("/settings")
def settings_save(
    request: Request,
    max_position_pct: str = Form(""),
    min_cash_pct: str = Form(""),
    max_trades_per_day: str = Form(""),
    max_order_amount: str = Form(""),
):
    try:
        guardrails.save(
            {
                "max_position_pct": max_position_pct,
                "min_cash_pct": min_cash_pct,
                "max_trades_per_day": max_trades_per_day,
                "max_order_amount": max_order_amount,
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
                },
                "guidance": user_guidance.load(),
                "saved": False,
                "guidance_saved": False,
                "guidance_cleared": False,
                "error": str(exc),
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
