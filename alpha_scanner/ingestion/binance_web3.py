"""Binance Web3 public market endpoints (read-only, no API key).

Endpoints and field semantics are taken from the official binance-skills-hub
skill docs (meme-rush 2.1, trading-signal 2.0, query-token-audit 1.4):
https://github.com/binance/binance-skills-hub/tree/main/skills/binance-web3

Coverage relevant here: Pump.fun (protocol 1001) and other Solana launchpads,
Four.meme / Flap / Open Four on BSC, Base launchpads.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from ..models import SecurityHit, SecurityReport, SecurityStatus, SmartMoneySignal, Stage, TokenSnapshot, Topic
from .http import SourceClient, SourceError, fnum, inum

log = logging.getLogger(__name__)

BASE = "https://web3.binance.com/bapi/defi"
MEME_RUSH_URL = f"{BASE}/v1/public/wallet-direct/buw/wallet/market/token/pulse/rank/list/ai"
TOPIC_RUSH_URL = f"{BASE}/v2/public/wallet-direct/buw/wallet/market/token/social-rush/rank/list/ai"
SMART_MONEY_URL = f"{BASE}/v1/public/wallet-direct/buw/wallet/web/signal/smart-money/ai"
AUDIT_URL = f"{BASE}/v1/public/wallet-direct/security/token/audit"

ICON_PREFIX = "https://bin.bnbstatic.com"

# Subset of the protocol table in meme-rush references/cli.md
PROTOCOLS = {
    1001: "Pump.fun", 1004: "LaunchLab", 1008: "Bonk", 1009: "Dynamic BC", 1010: "Moonshot",
    1011: "Jupiter Studio", 1012: "Bags", 1013: "Believer", 1017: "StonkFun",
    1005: "Raydium AMM", 1006: "Raydium CPMM", 1007: "Raydium CLMM", 1014: "Meteora DAMM V2",
    1015: "Meteora AMM", 1016: "Orca",
    2001: "Four.meme", 2002: "Flap", 2004: "Open Four", 2005: "Virtuals (BSC)", 2006: "genius.fun",
    2007: "genius.fun V2", 2008: "brew Standard", 2009: "brew Dividend", 2010: "brew Multipair",
    2012: "O1 Exchange Tax",
    3001: "Clanker", 3002: "Bankr", 3003: "Zora Creator", 3004: "Zora Content", 3006: "Flaunch",
    3008: "Virtuals (Base)", 3009: "Virtuals V5", 3010: "O1 Exchange", 3011: "Flap (Base)",
    3012: "O1 Exchange V3", 3013: "O1 Exchange Tax (Base)",
}

RANK_STAGE = {10: Stage.BONDING, 20: Stage.FINALIZING, 30: Stage.MIGRATED}

UA_HEADERS = {"Accept-Encoding": "identity", "User-Agent": "binance-web3/2.0 (Skill)"}


def _norm_addr(chain: str, addr: str) -> str:
    # EVM addresses are case-insensitive; Solana base58 is case-sensitive.
    return addr.lower() if chain != "CT_501" else addr


def _ms(v: Any) -> float | None:
    f = fnum(v)
    return f / 1000 if f else None


def _flag(v: Any) -> bool | None:
    if v is None:
        return None  # "not evaluated" per docs — never treat as safe
    return bool(inum(v))


def parse_meme_rush_row(row: dict[str, Any], rank_type: int) -> TokenSnapshot | None:
    chain, addr = row.get("chainId"), row.get("contractAddress")
    if not chain or not addr:
        return None
    protocol = inum(row.get("protocol"))
    migrated = inum(row.get("migrateStatus")) == 1
    stage = Stage.MIGRATED if migrated else RANK_STAGE.get(rank_type, Stage.UNKNOWN)
    socials = {k: v for k, v in (row.get("socials") or {}).items() if isinstance(v, str) and v}
    if row.get("twitterHandle"):
        socials.setdefault("twitter_handle", str(row["twitterHandle"]))
    narrative = row.get("narrativeText") or {}
    dev_position = inum(row.get("devPosition"))
    return TokenSnapshot(
        chain=chain, address=_norm_addr(chain, addr), sources=["binance_meme_rush"],
        symbol=row.get("symbol"), name=row.get("name"), protocol=protocol,
        platform=PROTOCOLS.get(protocol or -1, f"protocol {protocol}" if protocol else None), stage=stage,
        created_at=_ms(row.get("createTime")), migrated_at=_ms(row.get("migrateTime")),
        price_usd=fnum(row.get("price")), price_change_pct=fnum(row.get("priceChange")),
        market_cap_usd=fnum(row.get("marketCap")), liquidity_usd=fnum(row.get("liquidity")),
        volume_24h_usd=fnum(row.get("volume")), net_buy_usd=fnum(row.get("netBuy")),
        buys_24h=inum(row.get("countBuy")), sells_24h=inum(row.get("countSell")),
        holders=inum(row.get("holders")), progress_pct=fnum(row.get("progress")),
        top10_pct=fnum(row.get("holdersTop10Percent")), dev_pct=fnum(row.get("holdersDevPercent")),
        sniper_pct=fnum(row.get("holdersSniperPercent")), insider_pct=fnum(row.get("holdersInsiderPercent")),
        bundler_pct=fnum(row.get("bundlerHoldingPercent")), new_wallet_pct=fnum(row.get("newWalletHoldingPercent")),
        smart_money_holders=inum(row.get("smartMoneyHolders")), kol_holders=inum(row.get("kolHolders")),
        pro_holders=inum(row.get("proHolders")), sniper_count=inum(row.get("sniperCount")),
        dev_address=row.get("devAddress"), dev_sell_pct=fnum(row.get("devSellPercent")),
        dev_migrate_count=inum(row.get("devMigrateCount")),
        dev_sold_all=None if dev_position is None else dev_position == 2,
        tax_buy_pct=fnum(row.get("taxRateBuy")), tax_sell_pct=fnum(row.get("taxRateSell")),
        tag_dev_wash=_flag(row.get("tagDevWashTrading")), tag_insider_wash=_flag(row.get("tagInsiderWashTrading")),
        socials=socials, twitter_followers=inum((row.get("twitterInfo") or {}).get("followersCnt")),
        narrative=narrative.get("en") if isinstance(narrative, dict) else None,
    )


def parse_topic(row: dict[str, Any]) -> tuple[Topic, list[TokenSnapshot]]:
    chain = str(row.get("chainId"))
    name = (row.get("name") or {}).get("topicNameEn") or (row.get("name") or {}).get("topicNameCn") or "?"
    snaps: list[TokenSnapshot] = []
    for t in row.get("tokenList") or []:
        addr = t.get("contractAddress")
        if not addr:
            continue
        protocol = inum(t.get("protocol"))
        snaps.append(TokenSnapshot(
            chain=t.get("chainId") or chain, address=_norm_addr(chain, addr), sources=["binance_topic_rush"],
            symbol=t.get("symbol"), protocol=protocol or None, platform=PROTOCOLS.get(protocol or -1),
            stage=Stage.MIGRATED if inum(t.get("migrateStatus")) == 1 else (
                Stage.BONDING if inum(t.get("internal")) == 1 else Stage.UNKNOWN),
            created_at=_ms(t.get("createTime")), market_cap_usd=fnum(t.get("marketCap")),
            liquidity_usd=fnum(t.get("liquidity")), price_change_pct=fnum(t.get("priceChange24h")),
            net_buy_usd=fnum(t.get("netInflow")), trades_5m=inum(t.get("count5m")), trades_1h=inum(t.get("count1h")),
            unique_traders_5m=inum(t.get("uniqueTrader5m")), unique_traders_1h=inum(t.get("uniqueTrader1h")),
            holders=inum(t.get("holders")), kol_holders=inum(t.get("kolHolders")),
            smart_money_holders=inum(t.get("smartMoneyHolders")), topics=[name],
        ))
    topic = Topic(
        chain=chain, topic_id=str(row.get("topicId")), name=name,
        net_inflow_usd=fnum(row.get("topicNetInflow")), net_inflow_1h_usd=fnum(row.get("topicNetInflow1h")),
        summary=(row.get("aiSummary") or {}).get("aiSummaryEn"), tags=list(row.get("topicTags") or []),
        token_addresses=[s.address for s in snaps], created_at=_ms(row.get("createTime")),
    )
    return topic, snaps


def parse_smart_money(row: dict[str, Any]) -> SmartMoneySignal | None:
    chain, addr = row.get("chainId"), row.get("contractAddress")
    if not chain or not addr or row.get("signalId") is None:
        return None
    max_gain = fnum(row.get("maxGain"))  # decimal fraction per docs
    return SmartMoneySignal(
        chain=chain, address=_norm_addr(chain, addr), signal_id=str(row["signalId"]), ticker=row.get("ticker"),
        direction=str(row.get("direction") or "").lower(), smart_money_count=inum(row.get("smartMoneyCount")),
        trigger_time=_ms(row.get("signalTriggerTime")), alert_price=fnum(row.get("alertPrice")),
        current_price=fnum(row.get("currentPrice")), alert_market_cap=fnum(row.get("alertMarketCap")),
        max_gain_pct=max_gain * 100 if max_gain is not None else None, exit_rate_pct=fnum(row.get("exitRate")),
        status=row.get("status"), total_value_usd=fnum(row.get("totalTokenValue")),
    )


def parse_audit(chain: str, address: str, body: dict[str, Any]) -> SecurityReport:
    d = body.get("data") or {}
    if not body.get("success", False) or str(body.get("code")) != "000000":
        return SecurityReport(chain=chain, address=address, status=SecurityStatus.UNAVAILABLE,
                              error=f"code={body.get('code')} {body.get('message')}")
    # Per docs: data is only valid when BOTH flags are true; otherwise do not use risk fields.
    if not (d.get("hasResult") and d.get("isSupported")):
        return SecurityReport(chain=chain, address=address, status=SecurityStatus.UNSUPPORTED)
    extra = d.get("extraInfo") or {}
    hits = [
        SecurityHit(category=item.get("id", "?"), title=det.get("title", "?"),
                    severity=det.get("riskType", "CAUTION"), description=det.get("description"))
        for item in d.get("riskItems") or [] for det in item.get("details") or [] if det.get("isHit")
    ]
    return SecurityReport(
        chain=chain, address=address, status=SecurityStatus.CHECKED, risk_level=inum(d.get("riskLevel")),
        risk_enum=d.get("riskLevelEnum"), buy_tax_pct=fnum(extra.get("buyTax")), sell_tax_pct=fnum(extra.get("sellTax")),
        verified=extra.get("isVerified"), hits=hits,
    )


class BinanceWeb3Source:
    """Rate limits are not published for these endpoints; we stay conservative
    (60 req/min shared) and back off on business code 100004 (rate-limited)."""

    def __init__(self, timeout_s: float = 10.0, client: Any = None) -> None:
        self.http = SourceClient("binance_web3", rate_per_min=60, timeout_s=timeout_s, headers=UA_HEADERS, client=client)

    async def aclose(self) -> None:
        await self.http.aclose()

    def _data(self, body: Any, what: str) -> list[dict[str, Any]]:
        if not isinstance(body, dict) or str(body.get("code")) != "000000":
            code = body.get("code") if isinstance(body, dict) else "?"
            self.http.record_business_error(f"{what}: code {code}")
            raise SourceError(f"{what}: business code {code}")
        data = body.get("data")
        return data if isinstance(data, list) else []

    async def meme_rush(self, chain: str, rank_type: int, limit: int = 100, **filters: Any) -> list[TokenSnapshot]:
        body = await self.http.post(MEME_RUSH_URL, json={"chainId": chain, "rankType": rank_type,
                                                          "limit": min(limit, 100), **filters})
        rows = self._data(body, "meme-rush")
        return [s for s in (parse_meme_rush_row(r, rank_type) for r in rows) if s]

    async def topic_rush(self, chain: str, rank_type: int = 10, sort: int = 20) -> list[tuple[Topic, list[TokenSnapshot]]]:
        if chain not in ("56", "CT_501"):
            return []
        body = await self.http.get(TOPIC_RUSH_URL, params={"chainId": chain, "rankType": rank_type, "sort": sort,
                                                           "asc": "false"})
        return [parse_topic(r) for r in self._data(body, "topic-rush")]

    async def smart_money(self, chain: str, page_size: int = 100) -> list[SmartMoneySignal]:
        if chain not in ("56", "CT_501"):
            return []
        body = await self.http.post(SMART_MONEY_URL, json={"chainId": chain, "page": 1, "pageSize": min(page_size, 100)})
        return [s for s in (parse_smart_money(r) for r in self._data(body, "smart-money")) if s]

    async def token_audit(self, chain: str, address: str) -> SecurityReport:
        try:
            body = await self.http.post(AUDIT_URL, headers={"source": "agent"}, json={
                "binanceChainId": chain, "contractAddress": address, "requestId": str(uuid.uuid4())})
        except SourceError as e:
            return SecurityReport(chain=chain, address=address, status=SecurityStatus.UNAVAILABLE, error=str(e))
        return parse_audit(chain, address, body)
