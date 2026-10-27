# TIGER-POWER-Terminal: audit and reuse plan

- **Repository:** https://github.com/bzd6661/TIGER-POWER-Terminal
- **Commit reviewed:** `8f0a095ef4a0348802e5a9b56739ce46be40a617` (shallow clone, 2026-10-27)
- **Size:** about 7,000 lines of first-party JS (Node ESM) plus HTML. Windows-first (`.cmd`/`.vbs` launchers).
- **Method:** I read the code by hand (server, every signing path, scanner, automation), ran `npm audit`, and checked the README's safety claims against the code. I did **not** run it: it needs a logged-in Binance Agentic Wallet and BSC funds.

## 0. Licence: the blocker for code reuse

- The repo has **no LICENSE file** and no `license` field in `package.json`. By default that means all rights reserved. We **cannot copy, vendor or redistribute its code** without the author's written permission. The author's X handle is in the README: @tiger_web3.
- Exceptions inside the repo:
  - `contracts/TMTip.sol` is MIT (SPDX header).
  - `panel/vendor/lightweight-charts.js` is Apache-2.0 (TradingView).
- `@binance/agentic-wallet` (the `baw` CLI) is published as **`"license": "UNLICENSED"`**, which is proprietary. Install it from npm and invoke it. Never vendor or modify it.
- `binance/binance-skills-hub` has no LICENSE file either. We call the **public HTTP endpoints its docs describe**. We do not copy its scripts.

**Consequence:** "reuse" here means one of two things:
- (a) run TIGER-POWER **unmodified, as a separate local process**, for the BSC launchpad execution it already does, or
- (b) re-implement the *ideas* (protocol knowledge, safety patterns) independently in this repo.

This repo contains no TIGER-POWER code.

## 1. Architecture as found

```
Browser (127.0.0.1:4400) ──POST + x-panel-token──▶ panel/server.mjs
                                                    ├─ detect.mjs / scan.mjs / live.mjs / history.mjs  (read-only: BSC RPC, gang.fun, four.meme, fsi.taxed.fun, DexScreener, GeckoTerminal, GMGN via npx)
                                                    └─ spawn(node <script>.mjs …)  ──▶ baw CLI (Binance Agentic Wallet)
                                                         buy/fast/sell/claim (Flap "ID Agent"), fourbuy/foursell (four.meme Agent-ID),
                                                         gang/* (Gang V1/V2 via @gangfun/cli), dexbuy/dexsell (baw market-order),
                                                         transfer, tip
Background: autotransfer.mjs (auto-send after launch), extras.startAutoClaim, gang/v3watch.mjs --arm (auto-buy), fourauto.mjs (auto-buy)
```

Signing is done **inside Binance's Agentic Wallet**. No private key ever touches this code. Scripts build calldata, call `baw contract-call preview` (Binance simulation and risk engine), then `baw contract-call execute --requestId`. This is the strongest part of the design.

## 2. Security findings

Severity is for a single-user local install. "Verified" means I confirmed it in code.

| # | Severity | Finding | Evidence |
|---|---|---|---|
| F1 | **High** | **No licence**, so the code cannot legally be incorporated (see §0). | repo root |
| F2 | **High** | **Autonomous buying exists and is armed by flags, with no global loss limit.** `fourauto.mjs` buys the first 10 qualifying four.meme launches (defaults: 5 × up to 0.15 BNB, then 5 × 0.05 BNB). `gang/v3watch.mjs --arm` auto-buys the first 3 tokens of any newly detected launchpad (≤0.2 BNB each, both accounts). There is no daily-loss limit, no drawdown stop and no kill switch beyond killing the process. Each order still goes through `baw preview`. | `fourauto.mjs:9-60`, `gang/v3watch.mjs:5,216` |
| F3 | **High** | **Supply-chain execution with wallet access.** `v3watch.mjs` downloads the *latest* `@gangfun/cli` tarball from npm at runtime, extracts it, and points `GANG_CLI_CHAIN` at it. `gangcore.mjs` then `import`s that code inside a process that drives the wallet. `enrich.mjs` runs `npx -y gmgn-cli` (unpinned, `shell:true`). A compromised npm release would run with the ability to build transactions. | `gang/v3watch.mjs:168-185`, `gang/gangcore.mjs:12-19`, `panel/enrich.mjs:26` |
| F4 | Medium | **README claim "every trade goes through baw preview" is not fully true.** DEX swaps (`dexbuy`/`dexsell`, quote-asset swaps in `buy`/`fast`/`quoteswap`) call `baw market-order swap` directly. There is no `contract-call preview` and no audit pre-check (Binance's own skill requires `query-token-audit` before `market-order swap`). BNB transfers use `wallet send` with no preview (the README does admit this one). | `dexbuy.mjs:33-35`, `transfer.mjs:4` |
| F5 | Medium | **The panel's Flap buy route uses the "fast" script.** It skips the local `eth_call` simulation, the Probe rule checks and receipt waits. It keeps only `baw preview`. The safer `buy.mjs` exists but the panel doesn't call it. | `panel/server.mjs:180-182`, `fast.mjs:3-4` |
| F6 | Medium | **The auto-transfer target can be changed from the web panel.** The code comment says it can't. Any local process that can read `panel/.panel-token` (plaintext, persisted across restarts) can redirect irreversible launch-time transfers. Mitigations present: a double-entry field, a Telegram notice, and switches being disabled on change. | `panel/autotransfer.mjs:3` vs `panel/server.mjs:339-345` |
| F7 | Medium | **No per-trade cap by default.** `settings.maxBuyBnb` defaults to `0` (unlimited). `/api/run` hard-caps at 5 BNB, and percentage buys use quoted cost. There is no daily or total exposure limit anywhere. | `panel/extras.mjs:16-27`, `panel/server.mjs:475` |
| F8 | Medium | **`shell: true` with argument arrays.** The `baw` fallback in `buy.mjs`, `accounts.mjs:44` and `enrich.mjs` concatenates args into a shell string. Inputs reaching these paths are regex/`isAddress`-validated today, so I found **no exploitable injection**, but it's fragile. | `buy.mjs:66`, `accounts.mjs:44` |
| F9 | Low | **Dependency advisories** (`npm audit`): `ethers@6.13.4` via `ws` (moderate, fixed in 6.17.0). `@binance/agentic-wallet@1.11.0` via `zod` (high, memory exhaustion; no fix available in that package version; 1.12.0 is current). | `npm audit` |
| F10 | Low | **Medium-risk transactions can be pushed to App confirmation.** Gang inner-curve sells and opted-in transfers submit transactions Binance flagged `medium_*`, for the user to confirm in the Binance App. High risk is still blocked. This is user-controlled but lowers the bar. | `gang/gangv1sell.mjs:56-61`, `tip.mjs:49-52` |
| F11 | Info | Detection results are cached 20 s before routing an order. Stale routing is caught by on-chain simulation (documented and acceptable). | `panel/server.mjs:76-86` |

### Safeguards I verified as enforced

- Listens on `127.0.0.1` only (`server.listen(PORT, "127.0.0.1")`).
- A **Host allow-list** blocks DNS rebinding. An **Origin check** plus a **random token** on every POST blocks CSRF. The SSE stream also needs the token.
- Contract calls run local `eth_call` (in `buy`/`fourbuy`/`foursell`/`gang*`), then `baw preview`. They abort on simulation failure, on any `riskDetails`, or when `requireConfirmation` is set (unless the user opted in, see F10).
- **Exact-amount approvals only** (`approve(spender, exact)`; no `MaxUint256` anywhere). There's also an approvals page with revoke.
- Single-flight per account (`busy` set) prevents double orders from the panel.
- Wallet pinning: the address is recorded per account slot, and a login to a different wallet is logged out automatically.
- Gang deployer, Portal and Probe addresses are re-verified on-chain before use (`buy.mjs` portal check, Probe binding check).
- The tip contract has no owner and no storage, and the script checks its author address on-chain before paying.
- Secrets (`.env`, `baw2/.baw/`, wallet caches) are git-ignored. Account 1's credentials live in the Windows Credential Manager.

## 3. File-by-file reuse plan

"Reuse" here means running it as-is or porting the idea. Code is never copied (see §0).

| File / module | What it does | Verdict for this platform |
|---|---|---|
| `panel/detect.mjs` | Identifies the platform and stage of a BSC CA (four Agent-ID, Gang V1/V2, Flap "ID Agent", DEX) | **Port the idea** for the BSC execution-routing check in Phase 5. Re-derive contract addresses from official sources. |
| `panel/scan.mjs`, `panel/live.mjs`, `panel/logrpc.mjs` | On-chain event scanning (TokenCreated/TradeExecuted, Gang, Flap markets) with RPC fallbacks | **Port the idea later.** Phase 1 uses Binance Web3 `meme-rush` (covers Four.meme / Flap / Open Four / Pump.fun) instead. Raw logs become a Phase 2+ backfill source for point-in-time backtests. |
| `panel/history.mjs`, `panel/trades.mjs` | Trade history and candles (on-chain, gang.fun, fsi.taxed.fun, GeckoTerminal) | **Port the idea** for Phase 2 historical data. Their sources are unofficial or undocumented APIs; I'd treat them as best-effort. |
| `panel/track.mjs` | Wallet tracking (buy/add/trim/exit labels from balances) | **Port the idea** for smart-money wallet labelling (Phase 2). |
| `panel/quote.mjs`, `liq.mjs` | Pre-trade quotes; DexScreener min-liquidity gate | **Port the idea** for the Phase 3 slippage model and Phase 4 liquidity gate. |
| `panel/wallets.mjs`, `panel/session.mjs`, `accounts.mjs` | baw session status, QR login, wallet pinning, multi-account | **Run as-is** if TIGER-POWER is the BSC executor. **Port the pinning idea** for our execution adapter. |
| `buy.mjs`, `claim.mjs`, `sell.mjs`, `fourbuy.mjs`, `foursell.mjs`, `gang/gangv1*.mjs`, `gang/gangbuy.mjs`, `gang/gangv2sell.mjs`, `gang/gangcore.mjs` | Launchpad-specific execution through `baw` | **Phase 5 only. Do not integrate now.** If used, call them as an **external executor** behind our risk engine, with `--dry` for paper parity. Prefer `buy.mjs` over `fast.mjs` (F5). Pin `@gangfun/cli` (F3). |
| `fast.mjs` | Fast Flap buy (skips simulation and Probe checks) | **Do not use** for automated flow (F5). |
| `dexbuy.mjs`, `dexsell.mjs`, `quoteswap.mjs` | `baw market-order` swaps | **Modify before any use:** add a token audit and a quote/slippage check before the swap (F4). |
| `transfer.mjs`, `panel/autotransfer.mjs` | Token/BNB transfers, auto-send after launch | **Do not reuse** for automation. Irreversible, and the target is web-mutable (F6). |
| `fourauto.mjs`, `gang/v3watch.mjs --arm` | Autonomous buyers | **Do not reuse.** They bypass the staged validation this project requires (F2, F3). The watch-only mode of `v3watch` is a useful idea for discovery alerts. |
| `gang/gangwatch.mjs` | Position alerts to Telegram | **Port the idea** (Phase 3 paper-position alerts). |
| `panel/extras.mjs` | maxBuy setting, approvals list/revoke, auto-claim, latency stats | **Port the ideas:** approval audit and latency telemetry. maxBuy is superseded by the Phase 4 risk engine. |
| `panel/server.mjs` | HTTP API and process spawner | **Port the security pattern** (loopback, Host/Origin, token). Done in `alpha_scanner/api/server.py`. |
| `panel/index.html`, `scan.html`, `track.html` | UI | Not reused. |
| `tip.mjs`, `contracts/*` | Author tipping | Not relevant. |
| `baw2/` | Second baw install for account 2 | Not reused. |
| `env.mjs`, `.env.example` | Env loading | Not reused (Python config). |

## 4. Integration gaps I'm filling in this repo

- **Discovery is BSC-only and Agent-ID-only** in TIGER-POWER. We add Solana (Pump.fun and others) and Base via Binance Web3 `meme-rush`, plus `topic-rush` narratives and `smart-money` signals.
- **No scoring, no heatmaps, no explanations.** We add a 0–100 heuristic with per-component evidence, invalidation conditions and remaining risks.
- **No token security audit before DEX swaps.** We integrate Binance's token-audit API, and "unverified" is surfaced explicitly.
- **No backtesting or paper trading.** This repo has Phase 2 and 3 planned. Snapshots are stored point-in-time now so they can be replayed later.
- **No portfolio risk engine** (daily loss, drawdown, exposure, kill switch). That's Phase 4.

## 5. How to preserve the Binance Agentic Wallet integration

The working path is TIGER-POWER → `baw` CLI → Binance Agentic Wallet. Leave it untouched.

For Phase 5, our execution adapter will talk to `baw` directly, following the official skill rules (`binance-skills-hub/skills/binance-web3/binance-agentic-wallet`):
- `--json` always
- `contract-call preview`, then show the user, then `execute`
- token audit before `market-order swap`
- confirmation for each trade

TIGER-POWER can also stay as the operator's manual BSC terminal, side by side with this one.
