"""Feature extraction from a token's snapshot history.

Two kinds of inputs:
- provider windows (DexScreener m5/h1 volume and txns, Binance uniqueTrader5m/1h)
- our own time series: deltas between successive snapshots we stored.
Provider windows are preferred; history deltas are the fallback, so a token
seen for the first time has *less* evidence (lower confidence), not fake data.
"""

from __future__ import annotations

import time
from typing import Any

from ..models import SmartMoneySignal, TokenSnapshot, Topic


def _at_or_before(history: list[TokenSnapshot], ts: float) -> TokenSnapshot | None:
    best = None
    for s in history:
        if s.ts <= ts:
            best = s
        else:
            break
    return best


def window_rates(history: list[TokenSnapshot], field: str, window_s: float) -> tuple[float | None, float | None]:
    """(recent_rate, prior_rate) per second of a monotonic-ish field.

    recent = change over the last `window_s`, prior = change over the window
    before that. Uses only snapshots where the field is known.
    """
    pts = [s for s in history if getattr(s, field) is not None]
    if len(pts) < 2:
        return None, None
    last = pts[-1]
    a = _at_or_before(pts[:-1], last.ts - window_s * 0.8) or (pts[-2] if len(pts) >= 2 else None)
    if a is None or a.ts >= last.ts:
        return None, None
    recent = (getattr(last, field) - getattr(a, field)) / (last.ts - a.ts)
    b = _at_or_before([p for p in pts if p.ts < a.ts], a.ts - window_s * 0.8)
    prior = None
    if b is not None and b.ts < a.ts:
        prior = (getattr(a, field) - getattr(b, field)) / (a.ts - b.ts)
    return recent, prior


def _ratio(n: float | None, d: float | None) -> float | None:
    if n is None or d is None or d <= 0:
        return None
    return n / d


def price_change_since(history: list[TokenSnapshot], seconds: float) -> float | None:
    pts = [s for s in history if s.price_usd]
    if len(pts) < 2:
        return None
    last = pts[-1]
    ref = _at_or_before(pts[:-1], last.ts - seconds)
    if ref is None or last.ts - ref.ts < seconds * 0.5:
        return None
    return (last.price_usd / ref.price_usd - 1) * 100


def compute_features(
    history: list[TokenSnapshot],
    signals: list[SmartMoneySignal] | None = None,
    topics: list[Topic] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    if not history:
        raise ValueError("history must contain at least the current snapshot")
    now = now or time.time()
    history = sorted(history, key=lambda s: s.ts)
    cur = history[-1]
    signals = signals or []
    topics = topics or []
    f: dict[str, Any] = {
        "observations": len(history),
        "observed_span_min": round((cur.ts - history[0].ts) / 60, 1),
        "data_age_s": round(now - cur.ts, 1),
        "age_min": round((now - cur.created_at) / 60, 1) if cur.created_at else None,
    }

    # --- volume acceleration ---
    accel = None
    if cur.volume_5m_usd is not None and cur.volume_1h_usd:
        accel = (cur.volume_5m_usd * 12) / cur.volume_1h_usd
        f["volume_accel_basis"] = "provider 5m vs 1h"
    else:
        recent, prior = window_rates(history, "volume_24h_usd", 300)
        if recent is not None and prior is not None and prior > 0:
            accel = max(recent, 0) / prior
            f["volume_accel_basis"] = "snapshot deltas (5m vs prior 5m)"
        if recent is not None:
            f["volume_rate_usd_per_min"] = round(max(recent, 0) * 60, 2)
    f["volume_accel"] = round(accel, 3) if accel is not None else None
    f["volume_5m_usd"] = cur.volume_5m_usd
    f["volume_1h_usd"] = cur.volume_1h_usd
    f["volume_24h_usd"] = cur.volume_24h_usd

    # --- buyers / traders ---
    f["unique_traders_5m"] = cur.unique_traders_5m
    f["unique_traders_1h"] = cur.unique_traders_1h
    f["trader_accel"] = (round(cur.unique_traders_5m * 12 / cur.unique_traders_1h, 3)
                         if cur.unique_traders_5m is not None and cur.unique_traders_1h else None)
    buys, sells = (cur.buys_5m, cur.sells_5m) if cur.buys_5m is not None else (cur.buys_24h, cur.sells_24h)
    f["buy_ratio"] = round(buys / (buys + sells), 3) if buys is not None and sells is not None and buys + sells > 0 else None
    f["buy_ratio_window"] = "5m" if cur.buys_5m is not None else ("24h" if cur.buys_24h is not None else None)
    f["buys_window"] = buys
    trades_5m = cur.trades_5m if cur.trades_5m is not None else (
        cur.buys_5m + cur.sells_5m if cur.buys_5m is not None and cur.sells_5m is not None else None)
    f["trades_per_trader_5m"] = round(_ratio(trades_5m, cur.unique_traders_5m), 2) if _ratio(
        trades_5m, cur.unique_traders_5m) is not None else None
    f["net_buy_usd"] = cur.net_buy_usd

    # --- holders ---
    f["holders"] = cur.holders
    h_recent, h_prior = window_rates(history, "holders", 300)
    f["holder_growth_per_min"] = round(h_recent * 60, 3) if h_recent is not None else None
    f["holder_growth_accel"] = round(h_recent / h_prior, 3) if h_recent is not None and h_prior and h_prior > 0 else None
    f["volume_per_holder_usd"] = round(_ratio(cur.volume_24h_usd, cur.holders), 2) if _ratio(
        cur.volume_24h_usd, cur.holders) is not None else None

    # --- liquidity / size ---
    f["liquidity_usd"] = cur.liquidity_usd
    f["market_cap_usd"] = cur.market_cap_usd
    f["liq_to_mcap"] = round(_ratio(cur.liquidity_usd, cur.market_cap_usd), 4) if _ratio(
        cur.liquidity_usd, cur.market_cap_usd) is not None else None
    f["progress_pct"] = cur.progress_pct

    # --- momentum ---
    f["price_change_15m_pct"] = price_change_since(history, 900)
    f["price_change_1h_pct"] = price_change_since(history, 3600)
    f["price_change_24h_pct"] = cur.price_change_pct

    # --- smart money ---
    buys_sig = [s for s in signals if s.direction == "buy"]
    sells_sig = [s for s in signals if s.direction == "sell"]
    live = [s for s in buys_sig if (s.status or "").lower() in ("active", "valid", "")]
    f["smart_money_buy_signals"] = len(buys_sig)
    f["smart_money_live_buy_signals"] = len(live)
    f["smart_money_sell_signals"] = len(sells_sig)
    f["smart_money_wallets"] = max((s.smart_money_count or 0 for s in buys_sig), default=0) if buys_sig else None
    f["smart_money_exit_rate_pct"] = max((s.exit_rate_pct for s in buys_sig if s.exit_rate_pct is not None), default=None)
    f["smart_money_holders"] = cur.smart_money_holders
    f["kol_holders"] = cur.kol_holders

    # --- attention ---
    f["social_links"] = len([k for k in cur.socials if k != "twitter_handle"])
    f["twitter_followers"] = cur.twitter_followers
    names = set(cur.topics) | {t.name for t in topics if cur.address in t.token_addresses}
    f["topics"] = sorted(names)
    f["topic_inflow_1h_usd"] = max((t.net_inflow_1h_usd or 0 for t in topics if cur.address in t.token_addresses),
                                   default=None)
    return f
