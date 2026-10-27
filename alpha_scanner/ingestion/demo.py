"""Offline demo market. SYNTHETIC DATA — for UI/pipeline testing only.

Emits rows in the documented Binance Web3 / DexScreener response shapes and
runs them through the real parsers, so the demo exercises the same code path
as live mode. Archetypes cover organic momentum, wash trading, rugs,
honeypots and fading tokens so the filters have something to catch.
"""

from __future__ import annotations

import random
import string
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..models import SecurityReport, SmartMoneySignal, TokenSnapshot, Topic
from .binance_web3 import parse_audit, parse_meme_rush_row, parse_smart_money, parse_topic
from .dexscreener import aggregate_pairs

ARCHETYPES = ["organic", "organic", "smart", "wash", "rug", "honeypot", "fading", "late", "organic", "fading"]
NARRATIVES = ["AI agents", "Cat coins", "Political memes", "RWA", "Gaming"]
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


@dataclass
class DemoToken:
    chain: str
    address: str
    symbol: str
    kind: str
    protocol: int
    created: float
    holders: float = 5
    volume: float = 0
    price: float = 0.000004
    liquidity: float = 6_000
    buys: int = 0
    sells: int = 0
    progress: float = 1
    topic: str | None = None
    dev_pct: float = 4
    top10: float = 25
    smart: int = 0
    vol_5m: float = 0
    vol_1h_hist: list[float] = field(default_factory=list)


class SimClock:
    """Deterministic clock so offline multi-cycle runs see realistic time deltas."""

    def __init__(self, start: float | None = None) -> None:
        self.t = start or time.time()

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class DemoMarket:
    def __init__(self, seed: int = 7, n_tokens: int = 24, chains: tuple[str, ...] = ("CT_501", "56"),
                 clock: Callable[[], float] = time.time) -> None:
        self.rng = random.Random(seed)
        self.clock = clock
        self.tokens: list[DemoToken] = []
        now = clock()
        for i in range(n_tokens):
            chain = chains[i % len(chains)]
            kind = ARCHETYPES[i % len(ARCHETYPES)]
            addr = ("".join(self.rng.choice(B58) for _ in range(43)) + "pump" if chain == "CT_501"
                    else "0x" + "".join(self.rng.choice("0123456789abcdef") for _ in range(40)))
            sym = "".join(self.rng.choice(string.ascii_uppercase) for _ in range(self.rng.randint(3, 5)))
            t = DemoToken(chain=chain, address=addr, symbol=sym, kind=kind,
                          protocol=1001 if chain == "CT_501" else self.rng.choice([2001, 2002, 2004]),
                          created=now - self.rng.uniform(2, 600) * 60)
            t.topic = self.rng.choice(NARRATIVES) if kind in ("organic", "smart") and self.rng.random() < 0.7 else None
            if kind == "rug":
                t.dev_pct, t.top10 = 28, 70
            if kind == "wash":
                t.top10 = 55
            if kind in ("smart",):
                t.smart = self.rng.randint(2, 6)
            self.tokens.append(t)
        self.step_n = 0

    def step(self) -> None:
        self.step_n += 1
        r = self.rng
        for t in self.tokens:
            k = t.kind
            growth = {"organic": 1.06, "smart": 1.08, "wash": 1.005, "rug": 1.03, "honeypot": 1.05,
                      "fading": 0.97, "late": 1.25}[k]
            t.holders = max(3, t.holders * growth + r.uniform(0, 4) * (growth > 1))
            if k == "wash":
                vol = r.uniform(20_000, 60_000)
                t.buys += r.randint(150, 300)
                t.sells += r.randint(140, 290)
            else:
                vol = t.holders * r.uniform(15, 40) * (growth - 0.95) * 10
                t.buys += int(t.holders * r.uniform(0.05, 0.2))
                t.sells += int(t.holders * r.uniform(0.02, 0.12) * (2 if k == "fading" else 1))
            t.volume += max(vol, 0)
            t.vol_5m = max(vol, 0)
            t.vol_1h_hist = (t.vol_1h_hist + [t.vol_5m])[-12:]
            t.price *= growth ** 1.5 * r.uniform(0.98, 1.02)
            # bonding-curve reserves track market cap; DEX pools are seeded at migration
            t.liquidity = max(500.0, t.liquidity * (growth ** 0.8), t.price * 1e9 * 0.22 if k != "rug" else 0)
            if k == "rug" and self.step_n > 6:
                t.liquidity *= 0.3  # LP pulled
            t.progress = min(100, t.progress + (growth - 0.97) * 30)
            if k == "smart" and self.step_n % 3 == 0:
                t.smart += 1

    # --- documented response shapes ---
    def _meme_row(self, t: DemoToken) -> dict[str, Any]:
        supply = 1e9
        migrated = t.progress >= 100
        return {
            "chainId": t.chain, "contractAddress": t.address, "symbol": t.symbol, "name": f"{t.symbol} Demo",
            "price": str(t.price), "priceChange": str(round((t.price / 0.000004 - 1) * 100, 2)),
            "marketCap": str(t.price * supply), "liquidity": str(t.liquidity), "volume": str(t.volume),
            "netBuy": str((t.buys - t.sells) * t.price * 2e5), "holders": int(t.holders), "progress": f"{t.progress:.1f}",
            "protocol": t.protocol, "countBuy": t.buys, "countSell": t.sells,
            "holdersTop10Percent": f"{t.top10:.1f}", "holdersDevPercent": f"{t.dev_pct:.1f}",
            "holdersSniperPercent": "3.0", "holdersInsiderPercent": "2.0", "bundlerHoldingPercent": "1.5",
            "newWalletHoldingPercent": "40.0" if t.kind == "wash" else "12.0",
            "smartMoneyHolders": t.smart, "kolHolders": 1 if t.topic else 0,
            "devAddress": "demo-dev-" + t.address[:6], "devSellPercent": "80" if t.kind == "rug" and self.step_n > 6 else "0",
            "devMigrateCount": 0, "devPosition": None,
            "taxRateBuy": None, "taxRateSell": None,
            "tagDevWashTrading": 1 if t.kind == "wash" and self.step_n % 2 else None, "tagInsiderWashTrading": None,
            "socials": {"twitter": f"https://x.com/{t.symbol.lower()}"} if t.kind != "rug" else {},
            "twitterInfo": {"followersCnt": int(t.holders * 6)} if t.kind in ("organic", "smart") else None,
            "migrateStatus": 1 if migrated else 0, "migrateTime": 0,
            "createTime": int(t.created * 1000),
            "narrativeText": {"en": f"Demo narrative: {t.topic}"} if t.topic else None,
        }

    def _pairs(self, t: DemoToken) -> list[dict[str, Any]]:
        h1 = sum(t.vol_1h_hist) * (12 / max(len(t.vol_1h_hist), 1))
        b5 = int(t.vol_5m / 50) + (300 if t.kind == "wash" else 0)
        return [{"baseToken": {"address": t.address, "symbol": t.symbol, "name": t.symbol}, "priceUsd": str(t.price),
                 "liquidity": {"usd": t.liquidity}, "volume": {"m5": t.vol_5m, "h1": h1, "h24": t.volume},
                 "txns": {"m5": {"buys": b5, "sells": int(b5 * (0.9 if t.kind == "wash" else 0.4))},
                          "h1": {"buys": b5 * 10, "sells": b5 * 5}},
                 "marketCap": t.price * 1e9, "pairCreatedAt": int(t.created * 1000)}]


class DemoWeb3:
    def __init__(self, market: DemoMarket) -> None:
        self.m = market
        self._stepped_at = -1

    async def meme_rush(self, chain: str, rank_type: int, limit: int = 100) -> list[TokenSnapshot]:
        if rank_type == 10 and chain == self.m.tokens[0].chain:
            self.m.step()  # advance the synthetic market once per scan cycle
        out = []
        for t in self.m.tokens:
            if t.chain != chain:
                continue
            stage = 30 if t.progress >= 100 else (20 if t.progress >= 80 else 10)
            if stage == rank_type:
                out.append(parse_meme_rush_row(self.m._meme_row(t), rank_type))
        return [s for s in out if s][:limit]

    async def topic_rush(self, chain: str) -> list[tuple[Topic, list[TokenSnapshot]]]:
        out = []
        for name in NARRATIVES:
            toks = [t for t in self.m.tokens if t.chain == chain and t.topic == name]
            if not toks:
                continue
            out.append(parse_topic({"chainId": chain, "topicId": f"demo-{chain}-{name}", "name": {"topicNameEn": name},
                                    "topicNetInflow": str(sum(t.volume for t in toks) * 0.1),
                                    "topicNetInflow1h": str(sum(t.vol_5m for t in toks) * 2),
                                    "topicTags": ["Demo"], "tokenList": [
                                        {"chainId": chain, "contractAddress": t.address, "symbol": t.symbol,
                                         "uniqueTrader5m": int(t.holders * 0.15), "uniqueTrader1h": int(t.holders * 0.9),
                                         "count5m": int(t.holders * 0.3), "count1h": int(t.holders * 2)} for t in toks]}))
        return out

    async def smart_money(self, chain: str) -> list[SmartMoneySignal]:
        out = []
        for t in self.m.tokens:
            if t.chain == chain and t.kind == "smart":
                out.append(parse_smart_money({
                    "signalId": f"demo-{t.address[:10]}", "ticker": t.symbol, "chainId": chain, "contractAddress": t.address,
                    "direction": "buy", "smartMoneyCount": t.smart, "signalTriggerTime": int((self.m.clock() - 600) * 1000),
                    "alertPrice": str(t.price * 0.8), "currentPrice": str(t.price), "maxGain": "0.25", "exitRate": 10,
                    "status": "active"}))
        return [s for s in out if s]

    async def token_audit(self, chain: str, address: str) -> SecurityReport:
        t = next((t for t in self.m.tokens if t.address == address), None)
        if t is None or t.kind == "late":
            return parse_audit(chain, address, {"code": "000000", "success": True,
                                                "data": {"hasResult": False, "isSupported": False}})
        hit = t.kind == "honeypot"
        return parse_audit(chain, address, {"code": "000000", "success": True, "data": {
            "hasResult": True, "isSupported": True, "riskLevelEnum": "HIGH" if hit else "LOW", "riskLevel": 5 if hit else 1,
            "extraInfo": {"buyTax": "0", "sellTax": "99" if hit else "0", "isVerified": True},
            "riskItems": [{"id": "CONTRACT_RISK", "name": "Contract Risk", "details": [
                {"title": "Honeypot Risk Found" if hit else "Honeypot Risk Not Found",
                 "description": "A honeypot is a token that can be bought but not sold", "isHit": hit, "riskType": "RISK"}]}]}})


class DemoDex:
    def __init__(self, market: DemoMarket) -> None:
        self.m = market

    async def tokens(self, chain: str, addresses: list[str]) -> dict[str, TokenSnapshot]:
        out = {}
        for t in self.m.tokens:
            if t.chain == chain and t.address in addresses and t.progress >= 100:
                snap = aggregate_pairs(chain, t.address, self.m._pairs(t))
                if snap:
                    out[t.address] = snap
        return out


class DemoSpot:
    def __init__(self, seed: int = 3) -> None:
        self.rng = random.Random(seed)

    async def tickers_24h(self, quote: str = "USDT", top_n: int = 60) -> list[dict[str, Any]]:
        syms = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "PEPE", "WIF", "BONK", "SUI", "TRX", "ADA", "AVAX", "LINK",
                "TON", "SHIB", "FLOKI", "TAO", "FET", "RNDR", "INJ", "SEI", "TIA", "ARB", "OP", "NEAR", "APT", "JUP"]
        out = [{"symbol": s, "pair": s + quote, "change_pct": round(self.rng.gauss(0, 6), 2),
                "quote_volume": 10 ** self.rng.uniform(7, 10), "last_price": None, "trades": None} for s in syms]
        out.sort(key=lambda x: x["quote_volume"], reverse=True)
        return out[:top_n]
