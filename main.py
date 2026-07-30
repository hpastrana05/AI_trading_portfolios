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

BASE_DIR = Path(__file__).resolve().parent

_ACCOUNT_CACHE: dict[str, object] = {"ts": 0.0, "data": None}
_POSITIONS_CACHE: dict[str, object] = {"ts": 0.0, "data": None}


def _get_account_cached() -> dict:
    now = time.time()
    if _ACCOUNT_CACHE["data"] is None or now - float(_ACCOUNT_CACHE["ts"]) > 5:
        _ACCOUNT_CACHE["data"] = t212.get_account()
        _ACCOUNT_CACHE["ts"] = now
    return _ACCOUNT_CACHE["data"]  # type: ignore[return-value]


def _get_positions_cached() -> list[dict]:
    now = time.time()
    if _POSITIONS_CACHE["data"] is None or now - float(_POSITIONS_CACHE["ts"]) > 1:
        _POSITIONS_CACHE["data"] = t212.get_positions()
        _POSITIONS_CACHE["ts"] = now
    return _POSITIONS_CACHE["data"]  # type: ignore[return-value]


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
def performance_page(request: Request):
    try:
        account = _get_account_cached()
        performance.record_snapshot(account)
    except Exception:
        pass
    return templates.TemplateResponse(
        request,
        "performance.html",
        {"active": "performance", "env": config.T212_ENV},
    )


@app.get("/api/performance")
def performance_api(range: str = "max"):
    data = performance.build_performance(range)
    return JSONResponse(data)


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: int = 0, error: str | None = None):
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "active": "settings",
            "env": config.T212_ENV,
            "rules": guardrails.load(),
            "saved": bool(saved),
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
                "saved": False,
                "error": str(exc),
            },
            status_code=400,
        )


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
