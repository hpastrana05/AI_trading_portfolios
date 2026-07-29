import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import autopilot
import config
import journal
import memory
import t212

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
        autopilot.start(state.get("risk", "medium"))
    yield


app = FastAPI(title="AI Trading Autopilot", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    error = None
    account = None
    positions = []
    alloc = {}
    state = autopilot.load_state()

    try:
        account = _get_account_cached()
        positions = _get_positions_cached()
        alloc = autopilot.current_allocation(account, positions)
    except Exception as exc:
        error = str(exc)

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
            "error": error,
        },
    )


@app.post("/start")
def start_autopilot(risk: str = Form("medium")):
    autopilot.start(risk)
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
        pass  # error saved to state by autopilot
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=config.APP_HOST, port=config.APP_PORT, reload=True)
