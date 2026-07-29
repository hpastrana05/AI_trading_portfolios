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

## Run

```bash
uvicorn main:app --host 0.0.0.0 --port 8100
```

Or with Docker:

```bash
docker compose up -d --build
```

## How it works

1. Set **risk** (low / medium / high)
2. Click **Start autopilot** — AI runs immediately, then every `AUTOPILOT_INTERVAL_MINUTES`
3. Click **Stop** to pause
4. AI picks tickers, sizes positions, and executes trades automatically on Trading212
5. Every trade is logged with entry/exit, reason, when, and where
6. AI memory (`data/ai_memory.json`) stores thesis, plan, lessons, and thinking

## Pages

- **Dashboard** — start/stop, portfolio, status
- **Trade history** — all executed trades
- **AI memory** — how the AI is managing the portfolio

## Data files (in `./data/`)

| File | Purpose |
|------|---------|
| `trades.jsonl` | Every trade: entry, exit, reason, when, where |
| `ai_memory.json` | AI portfolio thesis, plan, lessons, thinking log |
| `autopilot_state.json` | Running/stopped, risk level, last run |
| `decisions.jsonl` | Full AI decision log per cycle |
| `instruments_cache.json` | Trading212 instrument list cache |

## Switch to live

```
T212_ENV=LIVE
```

Make sure live API keys are set in `.env`.
