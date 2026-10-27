# Platform plan: feasibility, architecture, methodology, validation

> Status: **Phase 1 (Alpha Scanner) is implemented in this repo.** Phases 2–5 are designed below and not built. Nothing in this repo can place a trade.

## 1. Integration feasibility matrix

Legend:
- ✅ verified: I read the official docs or source in this session.
- ⚠️ partial or unofficial.
- ❌ not available.
- 🧪 not live-tested from here, because this sandbox's network blocks those hosts. Each one needs a first live run on your machine.

| Integration | What it actually offers | Auth | Limits / fees | Status | Used for |
|---|---|---|---|---|---|
| **Binance Agentic Wallet** (`baw` CLI, npm `@binance/agentic-wallet` 1.12.0, proprietary) | Sign-in via QR. Chains (from `wallet chains`): BSC `56`, Ethereum `1`, Solana `CT_501`, Base `8453`, Arbitrum `42161`, Polygon `137`, Robinhood `4663`. Balances, `wallet send`, `market-order quote/swap/list`, `limit-order`, `contract-call preview/execute` (dev mode, ~7-day expiry), `sign-message`, `approvals list/revoke`, daily limit / slippage / MEV settings, DeFi, prediction markets. Signing happens inside Binance; keys never leave it. | Binance account + App confirmation; login up to 48 h (per TIGER-POWER README) | Wallet daily quota is shown in `wallet settings`. Swap fees and gas are per quote. No published API rate limit. | ✅ docs/source · 🧪 | Phase 5 on-chain execution (BSC/Solana/Base). **Not used by the scanner.** |
| **Binance Web3 public market APIs** (documented in `binance-skills-hub`: meme-rush 2.1, trading-signal 2.0, query-token-audit 1.4) | `meme-rush`: launchpad lifecycle feed (new / finalizing / migrated) on Solana, BSC and Base, including **Pump.fun (protocol 1001)**, Four.meme, Flap, Open Four. Fields: holders, top-10/dev/sniper/insider/bundler %, smart-money/KOL holders, dev behaviour, taxes, wash tags, socials, AI narrative. `topic-rush`: AI narratives with per-token 5m/1h unique traders. `smart-money`: buy/sell signals with wallet count, max gain, exit rate. `token audit`: honeypot/contract/trade/scam risk items, taxes, verified flag. | None (public) | Not published. Business code `100004` = rate-limited. Max 100 rows per call. We self-limit to 60 req/min. | ✅ docs · 🧪 | **Primary Phase 1 data and security source** |
| **Binance Spot API** | Klines (1s–1M), 24 h tickers, order book, trades. Market data needs no key. Trading needs HMAC/Ed25519 keys. | None for data; API key for trading | 6000 request-weight/min per IP. `ticker/24hr` (all symbols) = weight 80. Spot fee 0.1% base (lower with BNB/VIP). | ✅ public docs (from memory; re-verify) · 🧪 | Phase 1 spot heatmap. Phase 2 regime/backtest candles. Phase 5 **separate** exchange adapter. |
| **DexScreener** | `GET /tokens/v1/{chain}/{addrs}`: pools, liquidity, volume m5/h1/h6/h24, txns buys/sells per window | None | Documented 300 req/min for pair/token endpoints (60 for profile/boost endpoints). We use 120. | ⚠️ widely used public API (from memory; re-verify) · 🧪 | Pool liquidity and windowed volume for migrated tokens |
| **Pump.fun** | No official public developer API. `frontend-api*.pump.fun` is the site's private backend: unversioned and can change without notice. | — | — | ⚠️ | **Covered through Binance `meme-rush` protocol 1001** (bonding progress, migration, holders, dev). Direct API intentionally not used. |
| **PumpPortal** (third party) | Community WebSocket `wss://pumpportal.fun/api/data` (new tokens, trades, migrations). Trading API with a 0.5% fee (per secondary sources). | API key for trading | — | ⚠️ unverified: I couldn't reach its docs from here | Optional future low-latency Solana feed. Not implemented. |
| **FOMO (fomo.family)** | Consumer trading app (multi-chain, gasless). **No public developer API, SDK or docs found.** | — | — | ❌ | Not integrated. Revisit only if FOMO publishes an API. |
| **Telegram Bot API** | `sendMessage` | Bot token | ~30 msg/s global, 1 msg/s per chat recommended | ✅ | Alerts |
| Solana Tracker / Codex / Moralis (paid) | Pump.fun lifecycle REST and WebSocket | API key | Paid tiers | ⚠️ third-party docs | Fallback if Binance Web3 coverage proves insufficient |

### Verified documentation links

- Binance Agentic Wallet docs: https://developers.binance.com/en/docs/products/agentic-wallet/welcome (unreachable from this sandbox. Content was verified through the official skill repo below.)
- Binance skills hub (official): https://github.com/binance/binance-skills-hub
  - `skills/binance-web3/binance-agentic-wallet/SKILL.md` and its `references/` (commands, security pre-check, chains)
  - `skills/binance-web3/meme-rush/` (endpoint `POST https://web3.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/market/token/pulse/rank/list/ai`)
  - `skills/binance-web3/trading-signal/` (endpoint `…/buw/wallet/web/signal/smart-money/ai`)
  - `skills/binance-web3/query-token-audit/` (endpoint `…/security/token/audit`)
- Binance Spot market data: https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints
- DexScreener API: https://docs.dexscreener.com/api/reference
- Telegram Bot API: https://core.telegram.org/bots/api#sendmessage
- FOMO API search (none found): https://apis.io/providers/fomo/ , https://providers.apievangelist.com/providers/fomo/
- Pump.fun third-party APIs: https://docs.solanatracker.io/guides/pumpfun , https://docs.codex.io/launchpads/pump-fun.md , https://docs.moralis.com/changelog/pump-fun-api

**Execution routes are never interchangeable:**
- Pump.fun bonding-curve tokens trade on Solana programs (PumpPortal or Agentic Wallet `market-order` where Binance supports the pair).
- Four.meme / Flap / Gang use BSC contracts (`contract-call`).
- Exchange-listed assets use the Binance Spot API.

Every Phase 5 order re-resolves `(chain, address, stage)` to exactly one route.

## 2. Architecture and data flow

```
                    ┌───────────── Phase 1 (built) — no credentials ─────────────┐
 Binance Web3 ─┐    │ ingestion/    → scanner.collect() → storage (snapshots, point-in-time)
 (meme-rush,   ├──▶ │ http.py: rate limit, retry, circuit breaker, health                    │
 topic-rush,   │    │ analytics/features.py → analytics/filters.py (manipulation flags)      │
 smart-money,  │    │ scoring/security.py (audit → flags; unverified ≠ safe)                 │
 audit)        │    │ scoring/engine.py  (0–100 opportunity, risk, confidence, explanations) │
 DexScreener ──┤    │ alerts/engine.py   (rules, cooldown, Telegram)                         │
 Binance Spot ─┘    │ api/server.py + static dashboard (127.0.0.1 only)                     │
                    └────────────────────────────────────────────────────────────────────┘
                                         │ stored snapshots / scores
                    ┌────────────────────▼───────────────────────────────────────────────┐
                    │ Phase 2 backtest/ : point-in-time replay, cost model, metrics       │
                    │ Phase 3 paper/    : live signals → simulated fills/latency/failures │
                    │ Phase 4 risk/     : limits, kill switch, audit log (pure functions) │
                    │ Phase 5 execution/ (separate process, separate credentials):        │
                    │   onchain_baw.py  → baw CLI (Agentic Wallet)  [preview→approve→exec]│
                    │   binance_spot.py → Spot API (HMAC key, trade-only, IP-whitelisted) │
                    └─────────────────────────────────────────────────────────────────────┘
```

Stack:
- Python 3.11+, FastAPI, httpx, SQLAlchemy Core.
- SQLite by default; PostgreSQL via `ALPHA_DB_URL` (docker-compose provided).
- The dashboard is a dependency-free static page with a strict CSP. React only if the UI outgrows it.
- Redis isn't needed at this scale: the per-cycle cache lives in-process. Add it when the scanner and API run as separate replicas.

## 3. MVP scope and implementation sequence

| Phase | Scope | Exit / acceptance criteria (defined up front) |
|---|---|---|
| **1 Scanner** (done) | Ingestion from Binance Web3 (Solana/BSC/Base), DexScreener, Binance Spot. Point-in-time storage. Features, manipulation flags, security audit, scoring with explanations. Dashboard (feed, ranked candidates, filters, heatmaps, holder/wallet panel, watchlist, alerts, health). Telegram alerts. Tests. Offline demo mode. | 1. 7 days of continuous live running with ≥95% successful cycles and every source failure visible on `/api/health`. 2. Zero unhandled crashes. 3. Every candidate shows why it was detected, the evidence, what would invalidate it, and its risks. 4. Manual review of 50 alerts: at least 80% are not obvious wash/rug cases already visible at alert time. 5. ≥14 days of stored snapshots, enough to start Phase 2. |
| **2 Strategy & backtest** | Replay stored snapshots point-in-time (features computed only from data with `ts ≤ t`). Entry at the next observation's price plus a slippage model from liquidity; exits by rules; fees. Walk-forward weight tuning on a train window, evaluated on an untouched test window. Baselines: random entry from the same universe, and "buy every new token". | See §5. |
| **3 Paper trading** | Same signal code on live data. Simulated fills with latency, slippage from the live quote, failure injection, position sizing, SL/TP, exposure limits. Full signal/decision/rejection log. Expected vs realised price comparison. | See §5. |
| **4 Risk & security** | Risk engine (pure, unit-tested). Kill switch file plus API. Stale-data and simulation gates. Duplicate-order idempotency keys. Circuit breakers. Append-only audit log. Secrets via OS keychain or env-only, never logged. | 100% branch coverage on the risk engine; chaos tests (source down, stale data, partial fills) all reject safely. |
| **5 Controlled live** | Separate executor process. Per-trade human approval (preview shown). Tiny capital caps. Agentic Wallet for on-chain, Spot adapter for exchange. Autonomy only after explicit opt-in. | 30 approved live trades with realised slippage within the paper model's 80th percentile, and zero risk-rule violations. |

## 4. Token scoring and security methodology

**Opportunity score (0–100).** It's a weighted sum of eight normalised components (`config.ScoringWeights`). Missing inputs count as 0 and lower `confidence`.

| Component | Inputs | Normalisation |
|---|---|---|
| momentum (15) | Own 15 m/1 h price series, else provider 24 h change | `x/(x+40)`. Halved above +300% (late). |
| volume acceleration (15) | DexScreener `5m×12 / 1h`, else stored 24 h-volume deltas | `(ratio−1)/2`, clamped to 0..1 |
| unique buyers (15) | Unique traders 5 m (Binance), trader acceleration, buy ratio | Saturating, scaled by buy ratio |
| holder growth (10) | Δholders/min from stored snapshots | `x/(x+5)` |
| smart money (15) | Smart-money signal wallets, smart-money holders | Saturating, × (1 − exit rate) |
| liquidity (10) | Pool/curve liquidity, liq/mcap | Log scale $5k → $250k |
| attention (10) | Social links, X followers, narrative membership | Blend |
| upside (10) | Market cap | Log scale, inverted ($50k → $50M) |

**Risk score (0–100)**, built from three parts:
- Manipulation and structure flags: critical +35, warn +10.
- Audit points: risk level × 10, +50 honeypot, +15 per RISK item, +4 per CAUTION.
- A fixed **+30 when security is unverified** (unsupported, unavailable or not checked). These tokens are flagged `SECURITY_UNVERIFIED` and are never shown as safe.

**Hard blocks** (risk ≥ 90, no alerts, and in later phases no trades):
- Provider wash-trading tag
- Tax > 10%
- Audit level ≥ 4 or honeypot
- Liquidity < $5k
- Dev or bundler holding > 2× limit
- Data stale > 5 min

**Rank:** `rank = opportunity × (1 − risk/100) × (0.5 + 0.5 × confidence)`.

Manipulation heuristics:
- **Bot churn:** >8 trades per unique trader in 5 m.
- **Volume/holder mismatch:** >$2k of 24 h volume per holder when there are <500 holders.
- **One-sided flow:** ≥95% buys from fewer than 25 wallets.
- **Concentration:** top-10 / dev / sniper / insider / bundler / fresh wallets above limits.
- **Dev behaviour:** dev selling ≥50%, or serial launcher.
- **Thin liquidity:** liq/mcap below 3%.
- **Overextended:** price up more than 300% in 1 h.
- **Smart money exiting.**

Each flag is a heuristic and is labelled as one.

**What the score is not:** it is not a calibrated probability. Phase 2 will measure whether high-rank cohorts actually outperform baselines. If they don't, the weights change or the strategy is dropped.

## 5. Backtesting and paper-trading validation criteria (fixed before testing)

Backtest rules:
- **Point-in-time only.** Features use only data observed at or before decision time.
- **The universe includes tokens that died.** We store everything we saw, which avoids survivorship bias.
- **Entry happens at the next observation after the signal,** never at the signal snapshot price. On top of that entry price:
  - Slippage = `trade_size / liquidity × impact factor`, floored at 1% on bonding curves.
  - Fees: launchpad fee (Pump.fun ~1%, four.meme per contract), DEX fee, priority/gas.
- **Position size is capped at 1% of pool liquidity.**

Go/no-go for paper trading (all must hold on the out-of-sample window, ≥200 trades, ≥3 distinct market regimes):
1. Expected return per trade after costs > 0, with the 95% bootstrap CI lower bound > 0.
2. Profit factor ≥ 1.3.
3. Max drawdown ≤ 25% at the proposed sizing.
4. It beats the random-entry baseline from the same universe by a statistically significant margin (p < 0.05).
5. False-positive rate ≤ 60%. A false positive here means an alerted token that falls 50% below entry before rising 20% above it, with a rug-rate measurement.
6. Results hold when weights are perturbed by ±20%. If they don't, the setup is overfit.

Go/no-go for live (paper, ≥30 days, ≥100 trades):
1. Paper metrics within 1 standard deviation of the backtest.
2. Median realised − expected entry price ≤ the modelled slippage.
3. ≥90% of signals arrive early enough to be executable (the token is still within the entry rules at decision + latency).
4. Zero risk-engine violations.

A few profitable trades never advance a phase.

## 6. Complexity and operating cost estimates

| Item | Estimate |
|---|---|
| Phase 1 (this repo) | Done. About 2.5k lines of Python/JS plus tests. |
| Phase 2 backtester | 1–2 weeks of engineering, plus ≥2–4 weeks of data collection |
| Phase 3 paper trader | 1–2 weeks, plus ≥30 days of running |
| Phase 4 risk engine | 1 week |
| Phase 5 executors | 1–2 weeks per route (Agentic Wallet on-chain; Spot) |
| Data APIs | $0. Binance Web3, DexScreener and Binance Spot market data are free. Optional: own BSC/Solana RPC (NodeReal/Helius free tiers to ~$50–100/mo). Paid Pump.fun feeds (Solana Tracker/Codex) are ~$50–300/mo if needed. |
| Hosting | Runs on a laptop. A small VPS (2 vCPU / 4 GB) is ~$10–25/mo. Managed Postgres is optional, ~$15–30/mo. Storage: at 60 s polling with ~700 tokens per cycle, snapshots run about 0.5–1 GB/day (default retention 30 days, `snapshot_retention_days`). Use PostgreSQL beyond a few days, or raise `poll_interval_s`. |
| Trading costs (Phase 5) | Launchpad fees ~1%; DEX 0.25–1%; gas/priority fees; slippage, which is often the largest cost on micro-caps. Spot 0.1%. |

## 7. Assumptions, limitations, open dependencies

- **Not live-tested here.** This sandbox's network blocks Binance, DexScreener, Pump.fun and Telegram. Parsers are tested against the documented response shapes, and the whole pipeline is tested end-to-end on the synthetic demo market. The first live run on your machine is the real integration test. Watch `/api/health`.
- Binance Web3 endpoint rate limits aren't published. If they throttle, raise `poll_interval_s` or reduce `meme_rush_rank_types`.
- History-based features (holder growth, volume acceleration for bonding tokens) need a few cycles before they have data. Confidence reflects that.
- The 2 h feature window and 30-day snapshot retention are defaults (`history_window_s`, `snapshot_retention_days`).
- TIGER-POWER code reuse needs the author's licence (see `docs/TIGER-POWER-AUDIT.md`).
