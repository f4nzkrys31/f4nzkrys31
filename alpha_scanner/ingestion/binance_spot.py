"""Binance Spot public market data (no key needed for these endpoints).

Docs: https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints
- GET /api/v3/ticker/24hr (all symbols: request weight 80)
- GET /api/v3/klines (weight 2)
IP limit is 6000 request-weight / minute (check `X-MBX-USED-WEIGHT-1M`).
We use data-api.binance.vision, Binance's market-data-only mirror, by default.

Used for the exchange-listed heatmap and market-regime context only. Exchange
execution is a separate, later adapter and is not part of this package.
"""

from __future__ import annotations

from typing import Any

from .http import SourceClient, fnum

BASE = "https://data-api.binance.vision"
STABLES = {"USDC", "FDUSD", "TUSD", "USDP", "DAI", "BUSD", "USDE", "EUR", "AEUR", "XUSD", "USD1"}


class BinanceSpotSource:
    def __init__(self, timeout_s: float = 10.0, base: str = BASE, client: Any = None) -> None:
        # 24hr-all is weight 80 -> keep to a few calls a minute.
        self.http = SourceClient("binance_spot", rate_per_min=10, timeout_s=timeout_s, client=client)
        self.base = base

    async def aclose(self) -> None:
        await self.http.aclose()

    async def tickers_24h(self, quote: str = "USDT", top_n: int = 60) -> list[dict[str, Any]]:
        rows = await self.http.get(f"{self.base}/api/v3/ticker/24hr")
        out = []
        for r in rows if isinstance(rows, list) else []:
            sym = str(r.get("symbol", ""))
            if not sym.endswith(quote):
                continue
            base = sym[: -len(quote)]
            if base in STABLES or base.endswith(("UP", "DOWN", "BULL", "BEAR")):
                continue
            qv, chg = fnum(r.get("quoteVolume")), fnum(r.get("priceChangePercent"))
            if qv is None or chg is None or qv <= 0:
                continue
            out.append({"symbol": base, "pair": sym, "change_pct": chg, "quote_volume": qv,
                        "last_price": fnum(r.get("lastPrice")), "trades": r.get("count")})
        out.sort(key=lambda x: x["quote_volume"], reverse=True)
        return out[:top_n]

    async def klines(self, symbol: str, interval: str = "1h", limit: int = 500,
                     start_ms: int | None = None, end_ms: int | None = None) -> list[dict[str, float]]:
        params: dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": min(limit, 1000)}
        if start_ms:
            params["startTime"] = start_ms
        if end_ms:
            params["endTime"] = end_ms
        rows = await self.http.get(f"{self.base}/api/v3/klines", params=params)
        return [{"open_time": r[0] / 1000, "open": float(r[1]), "high": float(r[2]), "low": float(r[3]),
                 "close": float(r[4]), "volume": float(r[5]), "close_time": r[6] / 1000,
                 "quote_volume": float(r[7]), "trades": int(r[8])} for r in rows]
