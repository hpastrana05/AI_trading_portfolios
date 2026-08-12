# AI Trading Autopilot

Autonomous portfolio manager for Trading212, powered by Gemini.

**Start on demo. No AI system guarantees profits.**

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
mkdir -p data
```

## Run Demo and Live together

Demo and Live are separate processes (or containers) so you can test on practice while live stays up.

| Instance | Default URL | Data |
|----------|-------------|------|
| Demo | http://localhost:8100 | `data/demo/` |
| Live | http://localhost:8101 | `data/live/` |

### Docker (both)

```bash
mkdir -p data
docker compose up -d --build
```

- Start only one: `docker compose up -d demo` or `docker compose up -d live`
- Sidebar **Open DEMO/LIVE** keeps your current hostname and only changes the port (`8100` ↔ `8101`), so `pilab.local` at home and `pilab` over VPN both work without extra config

The container entrypoint fixes `./data` permissions on startup (common issue on Pi when the folder is owned by root).

### Local (both)

```bash
source venv/bin/activate
./scripts/run-both.sh
```

### Single process

```bash
# Demo (default)
T212_ENV=DEMO uvicorn main:app --host 0.0.0.0 --port 8100

# Live
T212_ENV=LIVE uvicorn main:app --host 0.0.0.0 --port 8101
```

Use the sidebar **Open DEMO** / **Open LIVE** link to jump between running instances.

## How it works

1. Set **risk** (low / medium / high)
2. Click **Start autopilot** — AI runs immediately, then every `AUTOPILOT_INTERVAL_MINUTES`
3. Click **Stop** to pause
4. AI picks tickers, sizes positions, and executes trades automatically on Trading212
5. Every trade is logged with entry/exit, reason, when, and where
6. AI memory stores thesis, plan, lessons, and thinking (per environment)

## Pages

- **Dashboard** — start/stop, portfolio, status
- **Trade history** — all executed trades
- **AI memory** — how the AI is managing the portfolio

## Project structure

Python code lives under `app/`. The root `main.py` is a thin entrypoint so `uvicorn main:app` keeps working (Docker, scripts, and local runs unchanged).

```
app/
├── core/           Shared config and time helpers
│   ├── config.py       .env, paths, Trading212/Gemini settings
│   └── timeutil.py     App timezone, scheduling, formatting
├── trading/        Broker integration
│   ├── t212.py         Portfolio view, orders, symbol resolution
│   └── api/            Low-level Trading212 REST wrappers
├── engine/         Autopilot loop and safety rules
│   ├── autopilot.py    Cycle orchestration (AI → trades → journal)
│   ├── circuit.py      Drawdown circuit breaker (safe mode / hard stop)
│   └── guardrails.py   Position limits, min cash, trade filters
├── ai/             Gemini and portfolio memory
│   ├── ai.py           Prompts, JSON decisions, usage logging
│   ├── memory.py       Thesis, plan, lessons, skip scars
│   └── user_guidance.py One-shot instructions for the next cycle
├── storage/        Local persistence
│   ├── performance.py  Snapshots, P&L charts, deposits/withdrawals
│   ├── journal.py      Executed trades log
│   └── usage.py        Gemini token/cost tracking
└── web/
    └── main.py         FastAPI app, routes, templates

main.py             Re-exports `app` from `app.web.main`
templates/          Jinja2 HTML
static/             CSS and JS
data/               Runtime state (see below)
scripts/            Helpers (e.g. run Demo + Live together)
```

**Import convention:** modules use absolute imports from the package, e.g. `from app.core import config`, `from app.engine import autopilot`.

## Data files

State is isolated per environment under `./data/demo/` and `./data/live/`:

| File | Purpose |
|------|---------|
| `trades.jsonl` | Every trade: entry, exit, reason, when, where |
| `ai_memory.json` | AI portfolio thesis, plan, lessons, thinking log |
| `autopilot_state.json` | Running/stopped, risk level, last run |
| `decisions.jsonl` | Full AI decision log per cycle |
| `instruments_cache.json` | Trading212 instrument list cache |
| `guardrails.json` | Hard limits for that environment |

Shared: `./data/ai_usage.jsonl` (Gemini usage, tagged with env).
