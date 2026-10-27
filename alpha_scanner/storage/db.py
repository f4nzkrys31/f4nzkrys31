"""Persistence (SQLAlchemy Core). SQLite by default, PostgreSQL via ALPHA_DB_URL.

Everything the scanner observes is stored with timestamps so Phase Two can
replay it point-in-time (no look-ahead) for backtests.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    delete,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Engine

from ..models import Candidate, SecurityReport, SmartMoneySignal, TokenSnapshot, Topic

md = MetaData()

tokens = Table(
    "tokens", md,
    Column("chain", String(16), primary_key=True), Column("address", String(64), primary_key=True),
    Column("symbol", String(64)), Column("name", String(256)), Column("platform", String(64)),
    Column("first_seen", Float, nullable=False), Column("last_seen", Float, nullable=False),
    Column("created_at", Float), Column("dev_address", String(64)),
)
snapshots = Table(
    "snapshots", md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chain", String(16), nullable=False), Column("address", String(64), nullable=False),
    Column("ts", Float, nullable=False), Column("data", JSON, nullable=False),
    Index("ix_snap_token_ts", "chain", "address", "ts"),
)
security = Table(
    "security_reports", md,
    Column("chain", String(16), primary_key=True), Column("address", String(64), primary_key=True),
    Column("checked_at", Float, nullable=False), Column("status", String(16), nullable=False),
    Column("data", JSON, nullable=False),
)
signals = Table(
    "smart_money_signals", md,
    Column("chain", String(16), primary_key=True), Column("signal_id", String(64), primary_key=True),
    Column("address", String(64), nullable=False), Column("trigger_time", Float), Column("seen_at", Float),
    Column("data", JSON, nullable=False), Index("ix_sig_token", "chain", "address"),
)
topics_t = Table(
    "topics", md,
    Column("chain", String(16), primary_key=True), Column("topic_id", String(64), primary_key=True),
    Column("ts", Float, nullable=False), Column("data", JSON, nullable=False),
)
candidates = Table(
    "candidates", md,
    Column("chain", String(16), primary_key=True), Column("address", String(64), primary_key=True),
    Column("updated_at", Float, nullable=False), Column("rank_score", Float), Column("opportunity_score", Float),
    Column("risk_score", Float), Column("confidence", Float), Column("blocked", Boolean),
    Column("data", JSON, nullable=False),
)
score_history = Table(
    "score_history", md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chain", String(16), nullable=False), Column("address", String(64), nullable=False),
    Column("ts", Float, nullable=False), Column("opportunity_score", Float), Column("risk_score", Float),
    Column("rank_score", Float), Column("confidence", Float), Column("blocked", Boolean),
    Column("price_usd", Float), Column("liquidity_usd", Float), Column("market_cap_usd", Float),
    Index("ix_score_token_ts", "chain", "address", "ts"),
)
alerts = Table(
    "alerts", md,
    Column("id", Integer, primary_key=True, autoincrement=True), Column("ts", Float, nullable=False),
    Column("chain", String(16), nullable=False), Column("address", String(64), nullable=False),
    Column("kind", String(32), nullable=False), Column("score", Float), Column("message", Text),
    Column("delivered", JSON),
)
watchlist = Table(
    "watchlist", md,
    Column("chain", String(16), primary_key=True), Column("address", String(64), primary_key=True),
    Column("note", String(500)), Column("added_at", Float, nullable=False),
)
cycles = Table(
    "scan_cycles", md,
    Column("id", Integer, primary_key=True, autoincrement=True), Column("started", Float), Column("finished", Float),
    Column("tokens", Integer), Column("errors", JSON),
)


class Store:
    def __init__(self, url: str) -> None:
        if url.startswith("sqlite:///") and url != "sqlite:///:memory:":
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        kw: dict[str, Any] = {"future": True}
        if url == "sqlite:///:memory:":
            from sqlalchemy.pool import StaticPool
            kw.update(connect_args={"check_same_thread": False}, poolclass=StaticPool)
        elif url.startswith("sqlite"):
            kw.update(connect_args={"check_same_thread": False})
        self.engine: Engine = create_engine(url, **kw)
        md.create_all(self.engine)

    # ---------- writes ----------
    def upsert_snapshot(self, s: TokenSnapshot) -> None:
        with self.engine.begin() as c:
            row = c.execute(select(tokens.c.first_seen).where(tokens.c.chain == s.chain, tokens.c.address == s.address)).first()
            vals = dict(symbol=s.symbol, name=s.name, platform=s.platform, last_seen=s.ts, created_at=s.created_at,
                        dev_address=s.dev_address)
            if row:
                c.execute(update(tokens).where(tokens.c.chain == s.chain, tokens.c.address == s.address)
                          .values(**{k: v for k, v in vals.items() if v is not None}))
            else:
                c.execute(insert(tokens).values(chain=s.chain, address=s.address, first_seen=s.ts, **vals))
            c.execute(insert(snapshots).values(chain=s.chain, address=s.address, ts=s.ts,
                                                 data=s.model_dump(mode="json", exclude_none=True)))

    def save_security(self, r: SecurityReport) -> None:
        with self.engine.begin() as c:
            c.execute(delete(security).where(security.c.chain == r.chain, security.c.address == r.address))
            c.execute(insert(security).values(chain=r.chain, address=r.address, checked_at=r.checked_at,
                                              status=r.status.value, data=r.model_dump(mode="json")))

    def save_signals(self, sigs: list[SmartMoneySignal], now: float | None = None) -> None:
        now = now or time.time()
        with self.engine.begin() as c:
            for s in sigs:
                c.execute(delete(signals).where(signals.c.chain == s.chain, signals.c.signal_id == s.signal_id))
                c.execute(insert(signals).values(chain=s.chain, signal_id=s.signal_id, address=s.address,
                                                 trigger_time=s.trigger_time, seen_at=now, data=s.model_dump(mode="json")))

    def save_topics(self, ts: list[Topic], now: float | None = None) -> None:
        now = now or time.time()
        with self.engine.begin() as c:
            for t in ts:
                c.execute(delete(topics_t).where(topics_t.c.chain == t.chain, topics_t.c.topic_id == t.topic_id))
                c.execute(insert(topics_t).values(chain=t.chain, topic_id=t.topic_id, ts=now, data=t.model_dump(mode="json")))

    def save_candidate(self, cand: Candidate) -> None:
        data = cand.model_dump(mode="json")
        snap = cand.snapshot
        with self.engine.begin() as c:
            c.execute(delete(candidates).where(candidates.c.chain == cand.chain, candidates.c.address == cand.address))
            c.execute(insert(candidates).values(
                chain=cand.chain, address=cand.address, updated_at=cand.updated_at, rank_score=cand.rank_score,
                opportunity_score=cand.opportunity_score, risk_score=cand.risk_score, confidence=cand.confidence,
                blocked=cand.blocked, data=data))
            c.execute(insert(score_history).values(
                chain=cand.chain, address=cand.address, ts=cand.updated_at, opportunity_score=cand.opportunity_score,
                risk_score=cand.risk_score, rank_score=cand.rank_score, confidence=cand.confidence, blocked=cand.blocked,
                price_usd=snap.get("price_usd"), liquidity_usd=snap.get("liquidity_usd"),
                market_cap_usd=snap.get("market_cap_usd")))

    def add_alert(self, chain: str, address: str, kind: str, score: float, message: str, delivered: dict[str, Any],
                  now: float | None = None) -> None:
        with self.engine.begin() as c:
            c.execute(insert(alerts).values(ts=now or time.time(), chain=chain, address=address, kind=kind, score=score,
                                            message=message, delivered=delivered))

    def record_cycle(self, started: float, finished: float, n: int, errors: list[str]) -> None:
        with self.engine.begin() as c:
            c.execute(insert(cycles).values(started=started, finished=finished, tokens=n, errors=errors))

    def watch_add(self, chain: str, address: str, note: str = "") -> None:
        with self.engine.begin() as c:
            c.execute(delete(watchlist).where(watchlist.c.chain == chain, watchlist.c.address == address))
            c.execute(insert(watchlist).values(chain=chain, address=address, note=note[:500], added_at=time.time()))

    def watch_remove(self, chain: str, address: str) -> None:
        with self.engine.begin() as c:
            c.execute(delete(watchlist).where(watchlist.c.chain == chain, watchlist.c.address == address))

    def prune(self, keep_s: float, now: float | None = None) -> None:
        cutoff = (now or time.time()) - keep_s
        with self.engine.begin() as c:
            c.execute(delete(snapshots).where(snapshots.c.ts < cutoff))

    # ---------- reads ----------
    def history(self, chain: str, address: str, since: float) -> list[TokenSnapshot]:
        with self.engine.connect() as c:
            rows = c.execute(select(snapshots.c.data).where(snapshots.c.chain == chain, snapshots.c.address == address,
                                                            snapshots.c.ts >= since).order_by(snapshots.c.ts)).all()
        return [TokenSnapshot.model_validate(_j(r[0])) for r in rows]

    def security_for(self, chain: str, address: str) -> SecurityReport | None:
        with self.engine.connect() as c:
            r = c.execute(select(security.c.data).where(security.c.chain == chain, security.c.address == address)).first()
        return SecurityReport.model_validate(_j(r[0])) if r else None

    def signals_for(self, chain: str, address: str, since: float) -> list[SmartMoneySignal]:
        with self.engine.connect() as c:
            rows = c.execute(select(signals.c.data).where(signals.c.chain == chain, signals.c.address == address,
                                                          signals.c.seen_at >= since)).all()
        return [SmartMoneySignal.model_validate(_j(r[0])) for r in rows]

    def recent_topics(self, since: float) -> list[Topic]:
        with self.engine.connect() as c:
            rows = c.execute(select(topics_t.c.data).where(topics_t.c.ts >= since)).all()
        return [Topic.model_validate(_j(r[0])) for r in rows]

    def candidates(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.engine.connect() as c:
            rows = c.execute(select(candidates.c.data).order_by(candidates.c.rank_score.desc()).limit(limit)).all()
        return [_j(r[0]) for r in rows]

    def candidate(self, chain: str, address: str) -> dict[str, Any] | None:
        with self.engine.connect() as c:
            r = c.execute(select(candidates.c.data).where(candidates.c.chain == chain, candidates.c.address == address)).first()
        return _j(r[0]) if r else None

    def score_series(self, chain: str, address: str, since: float) -> list[dict[str, Any]]:
        with self.engine.connect() as c:
            rows = c.execute(select(score_history).where(score_history.c.chain == chain, score_history.c.address == address,
                                                         score_history.c.ts >= since).order_by(score_history.c.ts)).mappings().all()
        return [dict(r) for r in rows]

    def new_tokens(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.engine.connect() as c:
            rows = c.execute(select(tokens).order_by(tokens.c.first_seen.desc()).limit(limit)).mappings().all()
        return [dict(r) for r in rows]

    def alerts(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.engine.connect() as c:
            rows = c.execute(select(alerts).order_by(alerts.c.ts.desc()).limit(limit)).mappings().all()
        return [dict(r) for r in rows]

    def last_alert(self, chain: str, address: str) -> dict[str, Any] | None:
        with self.engine.connect() as c:
            r = c.execute(select(alerts).where(alerts.c.chain == chain, alerts.c.address == address)
                          .order_by(alerts.c.ts.desc()).limit(1)).mappings().first()
        return dict(r) if r else None

    def watchlist(self) -> list[dict[str, Any]]:
        with self.engine.connect() as c:
            return [dict(r) for r in c.execute(select(watchlist).order_by(watchlist.c.added_at.desc())).mappings().all()]

    def is_watched(self, chain: str, address: str) -> bool:
        with self.engine.connect() as c:
            return c.execute(select(watchlist.c.address).where(watchlist.c.chain == chain,
                                                               watchlist.c.address == address)).first() is not None

    def last_cycle(self) -> dict[str, Any] | None:
        with self.engine.connect() as c:
            r = c.execute(select(cycles).order_by(cycles.c.id.desc()).limit(1)).mappings().first()
        return dict(r) if r else None


def _j(v: Any) -> Any:
    return json.loads(v) if isinstance(v, str) else v
