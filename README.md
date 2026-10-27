# Alpha Scanner: Phase One of a staged crypto intelligence platform

Alpha Scanner finds newly launched and early-momentum tokens on **Solana (Pump.fun and other launchpads), BSC (Four.meme, Flap, Open Four) and Base**. It scores each one for opportunity and risk, explains the score, and alerts you.

**It is read-only.** This repo contains no order placement and no signing code, and the scanner needs no trading credentials. Live trading comes last, only after backtesting, paper trading and the risk controls have passed the criteria in [`docs/PLAN.md`](docs/PLAN.md).

> Scores are heuristics, not probabilities of profit. Early-stage tokens frequently go to zero. Nothing here is investment advice.

## Documents

- [`docs/PLAN.md`](docs/PLAN.md) covers:
  - the integration feasibility matrix and verified documentation links
  - architecture and data flow
  - phases and acceptance criteria
  - the scoring and security methodology
  - backtest and paper-trading validation criteria
  - cost estimates
- [`docs/TIGER-POWER-AUDIT.md`](docs/TIGER-POWER-AUDIT.md) covers the security audit of `bzd6661/TIGER-POWER-Terminal`, a file-by-file reuse plan, the licence finding (no licence, so no code copied) and the integration gaps.

## What Phase One does

| Capability | Where |
|---|---|
| New-token feed (bonding / finalizing / migrated), narratives, smart-money signals | `ingestion/binance_web3.py` (Binance Web3 public APIs, documented in `binance/binance-skills-hub`) |
| Pool liquidity, 5m/1h volume, buy/sell txns | `ingestion/dexscreener.py` |
| Exchange-listed market heatmap | `ingestion/binance_spot.py` |
| Rate limits, retries, circuit breakers, source health | `ingestion/http.py` |
| Volume acceleration, unique-buyer and holder growth, buy pressure, momentum | `analytics/features.py` |
| Wash trading, bot churn, coordinated flow, concentration, dev behaviour, thin liquidity | `analytics/filters.py` |
| Token security audit. *Unverified is flagged, never "safe".* | `scoring/security.py` |
| Opportunity 0–100, risk 0–100, confidence, rank, plus why / evidence / invalidation / remaining risks | `scoring/engine.py` |
| Alerts with cooldown and score-jump re-alerts; Telegram | `alerts/engine.py` |
| Point-in-time storage (feeds the Phase 2 backtests) | `storage/db.py` |
| Dashboard: candidates with filters, feed, launchpad/narrative/spot heatmaps, holder and wallet panel, watchlist, alerts, health | `api/` |

## Quick start

Requires Python 3.11 or newer.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest                      # 36 tests

# Offline demo (synthetic market, labelled DEMO in the UI)
python -m alpha_scanner demo          # → http://127.0.0.1:8800
python -m alpha_scanner scan-once --demo --cycles 10

# Live, read-only
cp config/example.yaml config/local.yaml   # edit thresholds if you like
export ALPHA_CONFIG=config/local.yaml
export TG_BOT_TOKEN=... TG_CHAT_ID=...     # optional Telegram alerts
python -m alpha_scanner run           # scanner loop + dashboard
python -m alpha_scanner scan-once --top 20
```

Docker with PostgreSQL: `docker compose up --build`. The dashboard is published on `127.0.0.1:8800` only.

## Security properties

- **No trading code or credentials.** If exchange or wallet secrets are found in the scanner's environment, it logs a warning naming the variables (never their values).
- **Loopback only:**
  - A non-loopback `host` is refused (`ALPHA_ALLOW_REMOTE_BIND=1` overrides it, for use behind an auth proxy).
  - Host allow-list (blocks DNS rebinding).
  - Origin check plus a per-process token on every state-changing request.
  - Strict CSP.
  - The UI renders all token-provided text with `textContent`.
  - Telegram text is HTML-escaped.
- **Fail closed:** stale data, a provider wash tag, a honeypot or audit level ≥4, tax >10%, liquidity < $5k, or extreme dev/bundler holdings block a token from alerts.
- **Missing data lowers confidence; it is never treated as neutral or safe.**

## Status and limitations

- **Not yet run against live APIs.** This development sandbox's network blocks Binance, DexScreener and Telegram. Parsers are tested against the documented response shapes, and the full pipeline is tested end-to-end on the synthetic market. On your first live run, check the **Health** tab. Any source with errors or an open circuit shows up there.
- I haven't tested the Docker/PostgreSQL path here (no Docker in this sandbox). SQLite is tested.
- Pump.fun is covered through Binance Web3's launchpad feed (protocol 1001). The direct Pump.fun site API is unofficial and deliberately not used.
- FOMO has no public API, so it isn't integrated.

## Layout

```
alpha_scanner/
  config.py  models.py  scanner.py  __main__.py
  ingestion/  http.py binance_web3.py dexscreener.py binance_spot.py demo.py
  analytics/  features.py filters.py heatmap.py
  scoring/    engine.py security.py
  alerts/     engine.py
  storage/    db.py
  api/        server.py static/{index.html,app.js,app.css}
tests/        parsers, http, scoring/filters, alerts/api/scanner end-to-end
docs/         PLAN.md, TIGER-POWER-AUDIT.md
config/       example.yaml, example.env
```
