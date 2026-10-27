"""DexScreener public API (no key): pool liquidity, windowed volume and txns.

Endpoint: GET https://api.dexscreener.com/tokens/v1/{chainId}/{addr1,addr2,...}
(up to 30 addresses). DexScreener documents this family at 300 req/min; we use
a lower budget. Re-verify at https://docs.dexscreener.com/api/reference before
raising it.
"""

from __future__ import annotations

from typing import Any

from ..models import Stage, TokenSnapshot
from .http import SourceClient, fnum

URL = "https://api.dexscreener.com/tokens/v1/{chain}/{addresses}"
CHAIN_IDS = {"CT_501": "solana", "56": "bsc", "8453": "base"}
BATCH = 30


def aggregate_pairs(chain: str, address: str, pairs: list[dict[str, Any]]) -> TokenSnapshot | None:
    """Sum activity across all pools where the token is the base token."""
    norm = (lambda a: a) if chain == "CT_501" else str.lower
    mine = [p for p in pairs if norm(str((p.get("baseToken") or {}).get("address", ""))) == address]
    if not mine:
        return None
    deepest = max(mine, key=lambda p: fnum((p.get("liquidity") or {}).get("usd")) or 0)

    def total(path: tuple[str, ...]) -> float | None:
        vals = []
        for p in mine:
            v: Any = p
            for k in path:
                v = (v or {}).get(k) if isinstance(v, dict) else None
            f = fnum(v)
            if f is not None:
                vals.append(f)
        return sum(vals) if vals else None

    def itotal(path: tuple[str, ...]) -> int | None:
        t = total(path)
        return int(t) if t is not None else None

    created = [fnum(p.get("pairCreatedAt")) for p in mine if fnum(p.get("pairCreatedAt"))]
    base = deepest.get("baseToken") or {}
    socials = {}
    for s in (deepest.get("info") or {}).get("socials") or []:
        if s.get("type") and s.get("url"):
            socials[str(s["type"])] = str(s["url"])
    for w in (deepest.get("info") or {}).get("websites") or []:
        if w.get("url"):
            socials.setdefault("website", str(w["url"]))
    return TokenSnapshot(
        chain=chain, address=address, sources=["dexscreener"], symbol=base.get("symbol"), name=base.get("name"),
        stage=Stage.DEX, price_usd=fnum(deepest.get("priceUsd")),
        price_change_pct=fnum((deepest.get("priceChange") or {}).get("h24")),
        market_cap_usd=fnum(deepest.get("marketCap")) or fnum(deepest.get("fdv")),
        liquidity_usd=total(("liquidity", "usd")),
        volume_5m_usd=total(("volume", "m5")), volume_1h_usd=total(("volume", "h1")),
        volume_24h_usd=total(("volume", "h24")),
        buys_5m=itotal(("txns", "m5", "buys")), sells_5m=itotal(("txns", "m5", "sells")),
        buys_24h=itotal(("txns", "h24", "buys")), sells_24h=itotal(("txns", "h24", "sells")),
        trades_5m=_sum_known(itotal(("txns", "m5", "buys")), itotal(("txns", "m5", "sells"))),
        trades_1h=_sum_known(itotal(("txns", "h1", "buys")), itotal(("txns", "h1", "sells"))),
        created_at=min(created) / 1000 if created else None, socials=socials,
    )


class DexScreenerSource:
    def __init__(self, timeout_s: float = 10.0, client: Any = None) -> None:
        self.http = SourceClient("dexscreener", rate_per_min=120, timeout_s=timeout_s, client=client)

    async def aclose(self) -> None:
        await self.http.aclose()

    async def tokens(self, chain: str, addresses: list[str]) -> dict[str, TokenSnapshot]:
        ds_chain = CHAIN_IDS.get(chain)
        if not ds_chain:
            return {}
        out: dict[str, TokenSnapshot] = {}
        for i in range(0, len(addresses), BATCH):
            batch = addresses[i:i + BATCH]
            pairs = await self.http.get(URL.format(chain=ds_chain, addresses=",".join(batch)))
            if not isinstance(pairs, list):
                pairs = (pairs or {}).get("pairs") or []
            for a in batch:
                snap = aggregate_pairs(chain, a, pairs)
                if snap:
                    out[a] = snap
        return out


def _sum_known(*vals: int | None) -> int | None:
    known = [v for v in vals if v is not None]
    return sum(known) if known else None
