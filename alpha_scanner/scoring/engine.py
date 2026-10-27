"""Opportunity / risk scoring with human-readable explanations.

The opportunity score is a weighted heuristic in 0..100. It is NOT a
probability of success and has not been validated; Phase Two backtests exist
to measure whether it carries any predictive value.

Missing inputs contribute 0 (conservative) and lower `confidence`.
"""

from __future__ import annotations

import math
import time
from typing import Any

from ..analytics.filters import BLOCKING_CODES, detect_flags
from ..config import ScoringWeights, Thresholds
from ..models import Candidate, Component, Flag, SecurityReport, SecurityStatus, TokenSnapshot
from .security import security_flags

FLAG_POINTS = {"critical": 35.0, "warn": 10.0, "info": 0.0}


def sat(x: float, k: float) -> float:
    """Saturating map [0, inf) -> [0, 1): x / (x + k). k is the half-way point."""
    x = max(x, 0.0)
    return x / (x + k)


def log_scale(x: float, lo: float, hi: float) -> float:
    """0 at lo, 1 at hi, log-linear between, clamped."""
    if x <= lo:
        return 0.0
    if x >= hi:
        return 1.0
    return math.log(x / lo) / math.log(hi / lo)


def _fmt_usd(v: float | None) -> str:
    if v is None:
        return "unknown"
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(v) >= div:
            return f"${v / div:.1f}{suf}"
    return f"${v:.0f}"


def components(snap: TokenSnapshot, f: dict[str, Any], w: ScoringWeights) -> list[Component]:
    out: list[Component] = []

    # Momentum: prefer our own 15m/1h series, then provider 24h change.
    chg = next((f[k] for k in ("price_change_15m_pct", "price_change_1h_pct", "price_change_24h_pct")
                if f.get(k) is not None), None)
    basis = next((k for k in ("price_change_15m_pct", "price_change_1h_pct", "price_change_24h_pct")
                  if f.get(k) is not None), None)
    if chg is None:
        out.append(Component(name="momentum", weight=w.momentum, value=None, evidence="no price history"))
    else:
        v = sat(chg, 40)
        if chg > 300:
            v *= 0.5  # vertical moves are late, not early
        out.append(Component(name="momentum", weight=w.momentum, value=round(v, 3),
                             evidence=f"price {chg:+.1f}% ({basis.replace('price_change_', '').replace('_pct', '')})"))

    acc = f.get("volume_accel")
    out.append(Component(
        name="volume_acceleration", weight=w.volume_acceleration,
        value=None if acc is None else round(min(max((acc - 1) / 2, 0), 1), 3),
        evidence="no volume windows yet" if acc is None else f"volume run-rate {acc:.2f}x ({f.get('volume_accel_basis')})"))

    ut, ta, buys = f.get("unique_traders_5m"), f.get("trader_accel"), f.get("buys_window")
    if ut is not None:
        v = 0.7 * sat(ut, 40) + 0.3 * (min(max((ta or 1) - 1, 0), 1))
        ev = f"{ut} unique traders in 5m" + (f", {ta:.2f}x vs 1h avg" if ta else "")
    elif buys is not None:
        v = sat(buys, 150) * 0.8
        ev = f"{buys} buys ({f.get('buy_ratio_window')}) — unique-trader count unavailable"
    else:
        v, ev = None, "no buyer data"
    if v is not None and f.get("buy_ratio") is not None:
        v *= 0.5 + min(f["buy_ratio"], 0.8) / 1.6  # sell-dominated flow halves the score
        ev += f", buy ratio {f['buy_ratio']:.0%}"
    out.append(Component(name="unique_buyers", weight=w.unique_buyers, value=None if v is None else round(v, 3), evidence=ev))

    hg = f.get("holder_growth_per_min")
    out.append(Component(
        name="holder_growth", weight=w.holder_growth, value=None if hg is None else round(sat(hg, 5), 3),
        evidence=f"{f.get('holders')} holders" if hg is None else f"+{hg:.1f} holders/min ({f.get('holders')} total)"))

    sm_wallets, sm_holders = f.get("smart_money_wallets"), f.get("smart_money_holders")
    if sm_wallets is None and sm_holders is None:
        out.append(Component(name="smart_money", weight=w.smart_money, value=None, evidence="no smart-money data"))
    else:
        v = max(sat(sm_wallets or 0, 3), sat(sm_holders or 0, 3))
        er = f.get("smart_money_exit_rate_pct")
        if er is not None:
            v *= max(0.0, 1 - er / 100)
        ev = []
        if sm_wallets:
            ev.append(f"{sm_wallets} smart-money wallets in {f.get('smart_money_buy_signals')} buy signal(s)")
        if sm_holders:
            ev.append(f"{sm_holders} smart-money holders")
        if er is not None:
            ev.append(f"exit rate {er:.0f}%")
        out.append(Component(name="smart_money", weight=w.smart_money, value=round(v, 3),
                             evidence=", ".join(ev) or "no smart-money activity"))

    liq, ltm = snap.liquidity_usd, f.get("liq_to_mcap")
    if liq is None:
        out.append(Component(name="liquidity", weight=w.liquidity, value=None, evidence="liquidity unknown"))
    else:
        v = log_scale(liq, 5_000, 250_000)
        if ltm is not None:
            v = 0.7 * v + 0.3 * min(ltm / 0.15, 1)
        out.append(Component(name="liquidity", weight=w.liquidity, value=round(v, 3),
                             evidence=f"{_fmt_usd(liq)} liquidity" + (f" ({ltm:.1%} of mcap)" if ltm is not None else "")))

    soc, fol, topics = f.get("social_links") or 0, f.get("twitter_followers"), f.get("topics") or []
    v = 0.35 * min(soc / 3, 1) + 0.3 * (sat(fol, 5_000) if fol else 0) + 0.35 * (1.0 if topics else 0.0)
    ev = [f"{soc} social link(s)"]
    if fol:
        ev.append(f"{fol:,} X followers")
    if topics:
        ev.append("narrative: " + ", ".join(topics[:3]))
    out.append(Component(name="attention", weight=w.attention, value=round(v, 3), evidence="; ".join(ev)))

    mc = snap.market_cap_usd
    out.append(Component(
        name="upside", weight=w.upside, value=None if mc is None else round(1 - log_scale(mc, 50_000, 50_000_000), 3),
        evidence="market cap unknown" if mc is None else f"market cap {_fmt_usd(mc)} (room to re-rate)"))
    return out


def _invalidation(snap: TokenSnapshot, f: dict[str, Any], th: Thresholds) -> list[str]:
    out = []
    if snap.liquidity_usd:
        out.append(f"Liquidity falls below {_fmt_usd(max(th.min_liquidity_usd, snap.liquidity_usd * 0.6))} "
                   "(LP pulled or drained)")
    out.append("5-minute flow turns sell-dominated (buy ratio < 45%)")
    if f.get("unique_traders_5m"):
        out.append(f"Unique traders (5m) drop below {max(int(f['unique_traders_5m'] * 0.5), 1)}")
    if f.get("volume_accel") is not None:
        out.append("Volume run-rate decelerates below 1.0x of the hourly average")
    if f.get("smart_money_wallets"):
        out.append("Smart-money exit rate rises above 50% or sell signals appear")
    out.append(f"Dev wallet sells > {th.dev_dump_pct:.0f}% or top-10 holders exceed {th.max_top10_pct:.0f}%")
    out.append("Security audit returns high risk / honeypot, or taxes are raised")
    return out


def score(
    snap: TokenSnapshot,
    features: dict[str, Any],
    security: SecurityReport | None,
    weights: ScoringWeights,
    thresholds: Thresholds,
    now: float | None = None,
) -> Candidate:
    comps = components(snap, features, weights)
    total_w = sum(c.weight for c in comps) or 1.0
    known_w = sum(c.weight for c in comps if c.value is not None)
    opportunity = 100 * sum(c.weight * (c.value or 0) for c in comps) / total_w
    confidence = known_w / total_w

    flags: list[Flag] = detect_flags(snap, features, thresholds)
    sec_flags, sec_points = security_flags(security)
    flags += sec_flags
    risk = min(100.0, sec_points + sum(FLAG_POINTS[fl.severity] for fl in flags if fl.code not in
                                       ("SECURITY_UNVERIFIED",) and not fl.code.startswith("AUDIT_")))
    blocked = any(fl.code in BLOCKING_CODES for fl in flags)
    if blocked:
        risk = max(risk, 90.0)
    rank = opportunity * (1 - risk / 100) * (0.5 + 0.5 * confidence)

    ranked = sorted((c for c in comps if c.value), key=lambda c: c.weight * c.value, reverse=True)
    why = [f"{c.name.replace('_', ' ')}: {c.evidence}" for c in ranked[:3]]
    evidence = [f"{c.name}: {c.evidence}" + ("" if c.value is not None else " [missing]") for c in comps]
    remaining = [fl.message for fl in flags if fl.severity in ("critical", "warn")]
    missing = [c.name for c in comps if c.value is None]
    if missing:
        remaining.append("Missing data for: " + ", ".join(missing))
    remaining.append("Heuristic score only — not a validated probability of profit")

    return Candidate(
        chain=snap.chain, address=snap.address, symbol=snap.symbol, name=snap.name, platform=snap.platform,
        stage=snap.stage, updated_at=now or time.time(), opportunity_score=round(opportunity, 1),
        risk_score=round(risk, 1), rank_score=round(rank, 1), confidence=round(confidence, 2), blocked=blocked,
        security_status=security.status if security else SecurityStatus.NOT_CHECKED,
        components=comps, flags=flags, why_detected=why, evidence=evidence,
        invalidation=_invalidation(snap, features, thresholds), remaining_risks=remaining,
        snapshot=snap.model_dump(mode="json"), features=features,
    )
