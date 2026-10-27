"""CLI.

  python -m alpha_scanner run        # scanner loop + dashboard on http://127.0.0.1:8800
  python -m alpha_scanner demo       # same, with the offline synthetic market
  python -m alpha_scanner scan-once  # one cycle, print the top candidates
  python -m alpha_scanner serve      # dashboard only (reads the DB)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import time
from typing import Callable

import uvicorn

from .alerts.engine import AlertEngine
from .api.server import create_app
from .config import Settings, load_settings
from .scanner import Scanner
from .storage.db import Store


def build(settings: Settings, demo: bool, clock: Callable[[], float] = time.time) -> tuple[Store, Scanner]:
    store = Store(settings.db_url)
    if demo:
        from .ingestion.demo import DemoDex, DemoMarket, DemoSpot, DemoWeb3
        market = DemoMarket(chains=tuple(settings.sources.chains), clock=clock)
        web3, dex, spot = DemoWeb3(market), DemoDex(market), DemoSpot()
        # Demo: never send synthetic alerts to Telegram; lower the bar so the alert path is visible.
        alerts = AlertEngine(store, settings.alerts.model_copy(update={"telegram_bot_token": None, "min_score": 40}))
    else:
        from .ingestion.binance_spot import BinanceSpotSource
        from .ingestion.binance_web3 import BinanceWeb3Source
        from .ingestion.dexscreener import DexScreenerSource
        t = settings.sources.http_timeout_s
        web3, dex, spot = BinanceWeb3Source(t), DexScreenerSource(t), BinanceSpotSource(t)
        alerts = AlertEngine(store, settings.alerts)
    return store, Scanner(settings, store, web3, dex, spot, alerts=alerts, demo=demo, clock=clock)


async def _run(settings: Settings, demo: bool, serve_only: bool = False) -> None:
    store, scanner = build(settings, demo)
    app = create_app(settings, store, scanner)
    server = uvicorn.Server(uvicorn.Config(app, host=settings.host, port=settings.port, log_level="info"))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass
    tasks = [asyncio.create_task(server.serve())]
    if not serve_only:
        tasks.append(asyncio.create_task(scanner.run_forever(stop)))
    await stop.wait()
    server.should_exit = True
    await asyncio.gather(*tasks, return_exceptions=True)


async def _scan_once(settings: Settings, demo: bool, cycles: int, top: int) -> None:
    sim = None
    if demo:
        from .ingestion.demo import SimClock
        sim = SimClock()
    _, scanner = build(settings, demo, sim or time.time)
    cands = []
    for i in range(cycles):
        if i and sim:
            sim.advance(settings.sources.poll_interval_s)  # offline: simulate the poll interval
        elif i:
            await asyncio.sleep(settings.sources.poll_interval_s)
        cands = await scanner.run_cycle()
    cands.sort(key=lambda c: c.rank_score, reverse=True)
    for c in cands[:top]:
        print(json.dumps({"symbol": c.symbol, "chain": c.chain, "address": c.address, "platform": c.platform,
                          "rank": c.rank_score, "opportunity": c.opportunity_score, "risk": c.risk_score,
                          "confidence": c.confidence, "blocked": c.blocked, "security": c.security_status.value,
                          "why": c.why_detected, "flags": [f.code for f in c.flags]}))
    print(json.dumps({"status": scanner.status}, default=str))


def main() -> None:
    p = argparse.ArgumentParser(prog="alpha_scanner")
    p.add_argument("command", choices=["run", "demo", "scan-once", "serve"])
    p.add_argument("--config", help="YAML config path (default: $ALPHA_CONFIG)")
    p.add_argument("--demo", action="store_true", help="use the offline synthetic market")
    p.add_argument("--cycles", type=int, default=1, help="scan-once: number of cycles")
    p.add_argument("--top", type=int, default=15)
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings(a.config)
    demo = a.demo or a.command == "demo" or settings.demo_mode
    if demo:
        settings = settings.model_copy(update={"demo_mode": True})
        if settings.db_url == Settings().db_url:
            settings = settings.model_copy(update={"db_url": "sqlite:///data/demo.db"})
    if a.command == "scan-once":
        asyncio.run(_scan_once(settings, demo, a.cycles, a.top))
    else:
        asyncio.run(_run(settings, demo, serve_only=a.command == "serve"))


if __name__ == "__main__":
    main()
