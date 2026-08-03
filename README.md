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
