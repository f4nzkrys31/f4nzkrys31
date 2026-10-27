"""Local dashboard API (read-mostly). Binds to loopback; no trading endpoints exist.

Protections (mirrors and tightens the TIGER-POWER panel model):
- loopback bind enforced in config
- Host header allow-list (blocks DNS rebinding)
- Origin check + per-process token on every state-changing request (blocks CSRF)
- strict CSP; all token-provided strings are rendered with textContent in the UI
"""

from __future__ import annotations

import re
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..analytics.heatmap import launchpad_heatmap, narrative_heatmap
from ..config import SUPPORTED_CHAINS, Settings
from ..ingestion.http import HEALTH
from ..storage.db import Store

STATIC = Path(__file__).parent / "static"
ADDR_RE = re.compile(r"^(0x[0-9a-fA-F]{40}|[1-9A-HJ-NP-Za-km-z]{32,48})$")


class WatchBody(BaseModel):
    chain: str
    address: str
    note: str = Field(default="", max_length=500)


def _check_token_key(chain: str, address: str) -> tuple[str, str]:
    if chain not in SUPPORTED_CHAINS or not ADDR_RE.match(address):
        raise HTTPException(400, "bad chain or address")
    return chain, address.lower() if address.startswith("0x") else address


def create_app(settings: Settings, store: Store, scanner: Any = None) -> FastAPI:
    app = FastAPI(title="Alpha Scanner", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)
    token = secrets.token_hex(24)
    allowed_hosts = {f"127.0.0.1:{settings.port}", f"localhost:{settings.port}", f"[::1]:{settings.port}",
                     "127.0.0.1", "localhost"}

    @app.middleware("http")
    async def guard(request: Request, call_next):  # type: ignore[no-untyped-def]
        host = request.headers.get("host", "")
        if host not in allowed_hosts:
            return JSONResponse({"error": "host not allowed"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin not in (f"http://{host}",):
            return JSONResponse({"error": "origin not allowed"}, status_code=403)
        if request.method not in ("GET", "HEAD") and not secrets.compare_digest(
                request.headers.get("x-alpha-token", ""), token):
            return JSONResponse({"error": "missing or bad token"}, status_code=403)
        resp: Response = await call_next(request)
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        return resp

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        html = (STATIC / "index.html").read_text()
        return html.replace("__ALPHA_TOKEN__", token).replace("__DEMO__", "1" if settings.demo_mode else "0")

    @app.get("/api/candidates")
    def get_candidates(
        min_rank: float = 0, max_risk: float = 100, min_liquidity: float = 0, max_mcap: float | None = None,
        chain: str | None = None, include_blocked: bool = False, stage: str | None = None,
        sort: str = Query("rank_score", pattern="^(rank_score|opportunity_score|risk_score|confidence|updated_at)$"),
        limit: int = Query(100, le=500),
    ) -> list[dict[str, Any]]:
        out = []
        for c in store.candidates(limit=2000):
            snap = c.get("snapshot") or {}
            if c["rank_score"] < min_rank or c["risk_score"] > max_risk:
                continue
            if not include_blocked and c["blocked"]:
                continue
            if chain and c["chain"] != chain:
                continue
            if stage and c["stage"] != stage:
                continue
            liq = snap.get("liquidity_usd")
            if min_liquidity and (liq is None or liq < min_liquidity):
                continue
            mc = snap.get("market_cap_usd")
            if max_mcap is not None and (mc is None or mc > max_mcap):
                continue
            out.append(_summary(c))
        out.sort(key=lambda x: x.get(sort) or 0, reverse=sort != "risk_score")
        return out[:limit]

    @app.get("/api/feed")
    def feed(limit: int = Query(100, le=500)) -> list[dict[str, Any]]:
        rows = store.new_tokens(limit)
        for r in rows:
            c = store.candidate(r["chain"], r["address"])
            r["candidate"] = _summary(c) if c else None
        return rows

    @app.get("/api/token/{chain}/{address}")
    def token_detail(chain: str, address: str) -> dict[str, Any]:
        chain, address = _check_token_key(chain, address)
        c = store.candidate(chain, address)
        if not c:
            raise HTTPException(404, "unknown token")
        since = time.time() - 86_400
        return {
            "candidate": c,
            "score_history": store.score_series(chain, address, since),
            "security": (s.model_dump(mode="json") if (s := store.security_for(chain, address)) else None),
            "smart_money": [s.model_dump(mode="json") for s in store.signals_for(chain, address, since)],
            "watched": store.is_watched(chain, address),
        }

    @app.get("/api/heatmap")
    def heatmap() -> dict[str, Any]:
        cands = store.candidates(limit=2000)
        return {
            "launchpad": launchpad_heatmap(cands),
            "narratives": narrative_heatmap([t.model_dump(mode="json") for t in store.recent_topics(time.time() - 3600)]),
            "spot": getattr(scanner, "spot_heatmap", []) if scanner else [],
        }

    @app.get("/api/alerts")
    def get_alerts(limit: int = Query(100, le=500)) -> list[dict[str, Any]]:
        return store.alerts(limit)

    @app.get("/api/watchlist")
    def get_watchlist() -> list[dict[str, Any]]:
        rows = store.watchlist()
        for r in rows:
            c = store.candidate(r["chain"], r["address"])
            r["candidate"] = _summary(c) if c else None
        return rows

    @app.post("/api/watchlist")
    def add_watch(body: WatchBody) -> dict[str, Any]:
        chain, address = _check_token_key(body.chain, body.address)
        store.watch_add(chain, address, body.note)
        return {"ok": True}

    @app.delete("/api/watchlist/{chain}/{address}")
    def del_watch(chain: str, address: str) -> dict[str, Any]:
        chain, address = _check_token_key(chain, address)
        store.watch_remove(chain, address)
        return {"ok": True}

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        status = dict(getattr(scanner, "status", {}) or {})
        last = store.last_cycle()
        stale = not last or time.time() - (last["finished"] or 0) > settings.thresholds.stale_after_s
        return {"scanner": status, "last_cycle": last, "stale": stale, "demo": settings.demo_mode,
                "sources": [h.as_dict() for h in HEALTH.values()], "live_trading": "not implemented in Phase One"}

    return app


def _summary(c: dict[str, Any]) -> dict[str, Any]:
    snap = c.get("snapshot") or {}
    feats = c.get("features") or {}
    return {
        "chain": c["chain"], "address": c["address"], "symbol": c.get("symbol"), "name": c.get("name"),
        "platform": c.get("platform"), "stage": c.get("stage"), "rank_score": c["rank_score"],
        "opportunity_score": c["opportunity_score"], "risk_score": c["risk_score"], "confidence": c["confidence"],
        "blocked": c["blocked"], "security_status": c.get("security_status"), "updated_at": c["updated_at"],
        "price_usd": snap.get("price_usd"), "market_cap_usd": snap.get("market_cap_usd"),
        "liquidity_usd": snap.get("liquidity_usd"), "volume_24h_usd": snap.get("volume_24h_usd"),
        "holders": snap.get("holders"), "top10_pct": snap.get("top10_pct"), "age_min": feats.get("age_min"),
        "volume_accel": feats.get("volume_accel"), "holder_growth_per_min": feats.get("holder_growth_per_min"),
        "buy_ratio": feats.get("buy_ratio"), "smart_money_wallets": feats.get("smart_money_wallets"),
        "why_detected": c.get("why_detected", []),
        "flags": [f for f in c.get("flags", []) if f["severity"] != "info"],
    }
