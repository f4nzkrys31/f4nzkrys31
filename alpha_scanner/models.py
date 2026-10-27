"""Domain models. `None` always means *unknown*, never zero or safe."""

from __future__ import annotations

import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Stage(str, Enum):
    BONDING = "bonding"      # still on a launchpad bonding curve
    FINALIZING = "finalizing"  # bonding curve nearly complete
    MIGRATED = "migrated"    # graduated to a DEX pool
    DEX = "dex"              # DEX token with no launchpad info
    UNKNOWN = "unknown"


class TokenSnapshot(BaseModel):
    """One observation of a token at time `ts` from one or more sources."""

    chain: str
    address: str
    ts: float = Field(default_factory=time.time)
    sources: list[str] = Field(default_factory=list)
    symbol: str | None = None
    name: str | None = None
    protocol: int | None = None
    platform: str | None = None
    stage: Stage = Stage.UNKNOWN
    created_at: float | None = None      # unix seconds
    migrated_at: float | None = None

    price_usd: float | None = None
    price_change_pct: float | None = None
    market_cap_usd: float | None = None
    liquidity_usd: float | None = None
    volume_24h_usd: float | None = None
    volume_5m_usd: float | None = None
    volume_1h_usd: float | None = None
    net_buy_usd: float | None = None
    buys_24h: int | None = None
    sells_24h: int | None = None
    buys_5m: int | None = None
    sells_5m: int | None = None
    trades_5m: int | None = None
    trades_1h: int | None = None
    unique_traders_5m: int | None = None
    unique_traders_1h: int | None = None
    holders: int | None = None
    progress_pct: float | None = None

    top10_pct: float | None = None
    dev_pct: float | None = None
    sniper_pct: float | None = None
    insider_pct: float | None = None
    bundler_pct: float | None = None
    new_wallet_pct: float | None = None
    smart_money_holders: int | None = None
    kol_holders: int | None = None
    pro_holders: int | None = None
    sniper_count: int | None = None

    dev_address: str | None = None
    dev_sell_pct: float | None = None
    dev_migrate_count: int | None = None
    dev_sold_all: bool | None = None
    tax_buy_pct: float | None = None
    tax_sell_pct: float | None = None
    tag_dev_wash: bool | None = None
    tag_insider_wash: bool | None = None

    socials: dict[str, str] = Field(default_factory=dict)
    twitter_followers: int | None = None
    narrative: str | None = None
    topics: list[str] = Field(default_factory=list)

    @property
    def key(self) -> tuple[str, str]:
        return (self.chain, self.address)

    def merge(self, other: TokenSnapshot) -> TokenSnapshot:
        """Fill unknown fields of self from other (self wins where both known)."""
        data = self.model_dump()
        for k, v in other.model_dump().items():
            if k in ("sources", "topics"):
                data[k] = sorted(set(data[k]) | set(v))
            elif k == "socials":
                data[k] = {**v, **data[k]}
            elif data.get(k) is None or (k == "stage" and data[k] == Stage.UNKNOWN):
                data[k] = v
        return TokenSnapshot.model_validate(data)


class SecurityStatus(str, Enum):
    CHECKED = "checked"
    UNSUPPORTED = "unsupported"   # provider has no data for this token/chain
    UNAVAILABLE = "unavailable"   # provider unreachable / errored
    NOT_CHECKED = "not_checked"


class SecurityHit(BaseModel):
    category: str
    title: str
    severity: str  # "RISK" (critical) or "CAUTION"
    description: str | None = None


class SecurityReport(BaseModel):
    chain: str
    address: str
    status: SecurityStatus
    provider: str = "binance_web3_audit"
    checked_at: float = Field(default_factory=time.time)
    risk_level: int | None = None   # provider scale 0-5
    risk_enum: str | None = None
    buy_tax_pct: float | None = None
    sell_tax_pct: float | None = None
    verified: bool | None = None
    hits: list[SecurityHit] = Field(default_factory=list)
    error: str | None = None


class SmartMoneySignal(BaseModel):
    chain: str
    address: str
    signal_id: str
    ticker: str | None = None
    direction: str
    smart_money_count: int | None = None
    trigger_time: float | None = None
    alert_price: float | None = None
    current_price: float | None = None
    alert_market_cap: float | None = None
    max_gain_pct: float | None = None
    exit_rate_pct: float | None = None
    status: str | None = None
    total_value_usd: float | None = None


class Topic(BaseModel):
    chain: str
    topic_id: str
    name: str
    net_inflow_usd: float | None = None
    net_inflow_1h_usd: float | None = None
    summary: str | None = None
    tags: list[str] = Field(default_factory=list)
    token_addresses: list[str] = Field(default_factory=list)
    created_at: float | None = None


class Flag(BaseModel):
    code: str
    severity: str  # info | warn | critical
    message: str


class Component(BaseModel):
    name: str
    weight: float
    value: float | None  # normalised 0..1, None = missing data
    evidence: str


class Candidate(BaseModel):
    chain: str
    address: str
    symbol: str | None
    name: str | None
    platform: str | None
    stage: Stage
    updated_at: float
    opportunity_score: float     # 0..100 heuristic, NOT a probability
    risk_score: float            # 0..100, higher = riskier
    rank_score: float            # opportunity discounted by risk
    confidence: float            # share of weighted inputs that had data
    blocked: bool
    security_status: SecurityStatus
    components: list[Component]
    flags: list[Flag]
    why_detected: list[str]
    evidence: list[str]
    invalidation: list[str]
    remaining_risks: list[str]
    snapshot: dict[str, Any]
    features: dict[str, Any]
