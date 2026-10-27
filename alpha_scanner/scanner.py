"""Scan orchestrator: ingest -> persist -> features -> security -> score -> alert."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Protocol

from .alerts.engine import AlertEngine
from .analytics.features import compute_features
from .config import Settings
from .ingestion.http import SourceError
from .models import Candidate, SecurityReport, SecurityStatus, SmartMoneySignal, Stage, TokenSnapshot, Topic
from .scoring.engine import score
from .storage.db import Store

log = logging.getLogger(__name__)


class Web3Source(Protocol):
    async def meme_rush(self, chain: str, rank_type: int, limit: int = 100) -> list[TokenSnapshot]: ...
    async def topic_rush(self, chain: str) -> list[tuple[Topic, list[TokenSnapshot]]]: ...
    async def smart_money(self, chain: str) -> list[SmartMoneySignal]: ...
    async def token_audit(self, chain: str, address: str) -> SecurityReport: ...


class DexSource(Protocol):
    async def tokens(self, chain: str, addresses: list[str]) -> dict[str, TokenSnapshot]: ...


class SpotSource(Protocol):
    async def tickers_24h(self, quote: str = "USDT", top_n: int = 60) -> list[dict[str, Any]]: ...


class Scanner:
    def __init__(self, settings: Settings, store: Store, web3: Web3Source | None, dex: DexSource | None,
                 spot: SpotSource | None, alerts: AlertEngine | None = None, demo: bool = False,
                 clock: Callable[[], float] = time.time) -> None:
        self.s, self.store = settings, store
        self.clock = clock
        self.web3, self.dex, self.spot = web3, dex, spot
        self.alerts = alerts or AlertEngine(store, settings.alerts)
        self.demo = demo
        self.spot_heatmap: list[dict[str, Any]] = []
        self.status: dict[str, Any] = {"cycles": 0, "last_started": None, "last_finished": None, "last_errors": [],
                                       "demo": demo, "running": False}

    async def _try(self, errors: list[str], what: str, coro: Any) -> Any:
        try:
            return await coro
        except SourceError as e:
            errors.append(f"{what}: {e}")
            log.warning("%s failed: %s", what, e)
            return None

    async def collect(self, errors: list[str], now: float) -> tuple[dict[tuple[str, str], TokenSnapshot], list[Topic]]:
        snaps: dict[tuple[str, str], TokenSnapshot] = {}
        all_topics: list[Topic] = []

        def put(s: TokenSnapshot) -> None:
            s = s.model_copy(update={"ts": now})
            snaps[s.key] = snaps[s.key].merge(s) if s.key in snaps else s

        src = self.s.sources
        if self.web3 and src.binance_web3_enabled:
            for chain in src.chains:
                for rt in src.meme_rush_rank_types:
                    for s in await self._try(errors, f"meme-rush {chain}/{rt}",
                                             self.web3.meme_rush(chain, rt, src.meme_rush_limit)) or []:
                        put(s)
                for topic, tsnaps in await self._try(errors, f"topic-rush {chain}", self.web3.topic_rush(chain)) or []:
                    all_topics.append(topic)
                    for s in tsnaps:
                        put(s)
                sigs = await self._try(errors, f"smart-money {chain}", self.web3.smart_money(chain)) or []
                self.store.save_signals(sigs, now)
                for sig in sigs:
                    if (sig.chain, sig.address) not in snaps:
                        put(TokenSnapshot(chain=sig.chain, address=sig.address, symbol=sig.ticker,
                                          sources=["binance_smart_money"], price_usd=sig.current_price))
        self.store.save_topics(all_topics, now)

        if self.dex and src.dexscreener_enabled and snaps:
            # Enrich DEX-traded tokens first (bonding-curve tokens have no pools yet), then by net buy.
            order = sorted(snaps.values(), key=lambda s: (s.stage not in (Stage.MIGRATED, Stage.DEX, Stage.UNKNOWN),
                                                          -(s.net_buy_usd or 0)))
            by_chain: dict[str, list[str]] = {}
            for s in order[: src.dexscreener_enrich_top_n]:
                by_chain.setdefault(s.chain, []).append(s.address)
            for chain, addrs in by_chain.items():
                found = await self._try(errors, f"dexscreener {chain}", self.dex.tokens(chain, addrs)) or {}
                for addr, ds in found.items():
                    base = snaps[(chain, addr)]
                    # Launchpad data (stage, holders) wins; DexScreener fills windows and pool liquidity.
                    merged = base.merge(ds.model_copy(update={
                        "ts": now, "stage": Stage.DEX if base.stage == Stage.UNKNOWN else Stage.UNKNOWN}))
                    for f in ("liquidity_usd", "volume_5m_usd", "volume_1h_usd", "buys_5m", "sells_5m",
                              "trades_5m", "trades_1h"):
                        if getattr(ds, f) is not None:
                            merged = merged.model_copy(update={f: getattr(ds, f)})
                    snaps[(chain, addr)] = merged
        return snaps, all_topics

    def _score_one(self, snap: TokenSnapshot, topics: list[Topic], now: float) -> Candidate:
        since = now - self.s.history_window_s
        hist = self.store.history(snap.chain, snap.address, since)
        if not hist or hist[-1].ts < snap.ts:
            hist.append(snap)
        feats = compute_features(hist, self.store.signals_for(snap.chain, snap.address, since), topics, now=now)
        sec = self.store.security_for(snap.chain, snap.address)
        return score(snap, feats, sec, self.s.weights, self.s.thresholds, now=now)

    async def run_cycle(self) -> list[Candidate]:
        started = self.clock()
        self.status.update(running=True, last_started=started)
        errors: list[str] = []
        snaps, topics = await self.collect(errors, started)
        for s in snaps.values():
            self.store.upsert_snapshot(s)

        cands = {k: self._score_one(s, topics, started) for k, s in snaps.items()}

        # Audit the most promising tokens whose report is missing or older than the TTL.
        if self.web3 and self.s.sources.binance_web3_enabled:
            ttl = self.s.thresholds.audit_ttl_s
            todo = []
            for c in sorted(cands.values(), key=lambda c: c.opportunity_score, reverse=True):
                rep = self.store.security_for(c.chain, c.address)
                fresh = rep and started - rep.checked_at < (ttl if rep.status == SecurityStatus.CHECKED else ttl / 6)
                if not fresh:
                    todo.append(c)
                if len(todo) >= self.s.sources.audit_top_n:
                    break
            for c in todo:
                rep = await self.web3.token_audit(c.chain, c.address)
                if rep.status == SecurityStatus.UNAVAILABLE:
                    errors.append(f"audit {c.chain}/{c.address}: {rep.error}")
                self.store.save_security(rep)
                cands[(c.chain, c.address)] = self._score_one(snaps[(c.chain, c.address)], topics, started)

        for c in cands.values():
            self.store.save_candidate(c)

        if self.spot and self.s.sources.binance_spot_enabled:
            heat = await self._try(errors, "binance spot", self.spot.tickers_24h(top_n=self.s.sources.spot_heatmap_top_n))
            if heat is not None:
                self.spot_heatmap = heat

        fired = await self.alerts.process(sorted(cands.values(), key=lambda c: c.rank_score, reverse=True), now=started)
        finished = self.clock()
        self.store.record_cycle(started, finished, len(cands), errors)
        self.store.prune(keep_s=max(self.s.snapshot_retention_days * 86_400, self.s.history_window_s), now=started)
        self.status.update(running=False, last_finished=finished, last_errors=errors, cycles=self.status["cycles"] + 1,
                           last_tokens=len(cands), last_alerts=len(fired))
        log.info("cycle done: %d tokens, %d alerts, %d errors, %.1fs", len(cands), len(fired), len(errors),
                 finished - started)
        return list(cands.values())

    async def run_forever(self, stop: asyncio.Event | None = None) -> None:
        stop = stop or asyncio.Event()
        while not stop.is_set():
            try:
                await self.run_cycle()
            except Exception:  # keep the loop alive; the error is visible in logs and health
                log.exception("scan cycle crashed")
                self.status.update(running=False, last_errors=["cycle crashed — see logs"])
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.s.sources.poll_interval_s)
            except asyncio.TimeoutError:
                pass
