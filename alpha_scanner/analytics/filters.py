"""Manipulation and structural-risk heuristics.

Each check returns a Flag. These are heuristics with false positives and
negatives; they reduce, not eliminate, exposure to wash trading, bots,
coordinated shilling and rugs. Severity:
  critical -> candidate is blocked from alerts (and later from trading)
  warn     -> adds to risk score
  info     -> shown as context only
"""

from __future__ import annotations

from typing import Any

from ..config import Thresholds
from ..models import Flag, TokenSnapshot

BLOCKING_CODES = {
    "WASH_TAGGED", "TAX_EXTREME", "AUDIT_HIGH_RISK", "AUDIT_HONEYPOT", "LIQUIDITY_TOO_LOW", "DATA_STALE",
    "DEV_HOLDING_EXTREME", "BUNDLER_HOLDING_EXTREME",
}


def detect_flags(snap: TokenSnapshot, feat: dict[str, Any], th: Thresholds) -> list[Flag]:
    flags: list[Flag] = []
    add = lambda code, sev, msg: flags.append(Flag(code=code, severity=sev, message=msg))  # noqa: E731

    # Provider-tagged wash trading (null = not evaluated, so absence proves nothing)
    if snap.tag_dev_wash or snap.tag_insider_wash:
        who = " & ".join(w for w, t in (("dev", snap.tag_dev_wash), ("insider", snap.tag_insider_wash)) if t)
        add("WASH_TAGGED", "critical", f"Binance Web3 tagged {who} wash trading")

    # Bot churn: many trades per distinct trader in 5 minutes
    tpt = feat.get("trades_per_trader_5m")
    if tpt is not None and tpt > th.max_trades_per_trader_5m:
        add("BOT_CHURN", "warn", f"{tpt:.1f} trades per unique trader in 5m (> {th.max_trades_per_trader_5m}) — "
                                 "likely bots / wash volume")

    # Volume far out of proportion to holder base
    vph = feat.get("volume_per_holder_usd")
    if vph is not None and vph > th.max_volume_per_holder_usd and (snap.holders or 0) < 500:
        add("VOLUME_HOLDER_MISMATCH", "warn",
            f"${vph:,.0f} 24h volume per holder with only {snap.holders} holders — volume may be recycled")

    # One-sided flow from a small crowd looks coordinated
    br, buys = feat.get("buy_ratio"), feat.get("buys_window")
    traders = feat.get("unique_traders_5m") or snap.holders
    if br is not None and br >= th.one_sided_buy_ratio and traders is not None and traders < th.one_sided_min_traders \
            and (buys or 0) >= 20:
        add("ONE_SIDED_FLOW", "warn", f"{br:.0%} buys from only {traders} wallets — possible coordinated pump")

    # Concentration
    for field, limit, code, label in (
        ("top10_pct", th.max_top10_pct, "TOP10_CONCENTRATION", "Top-10 holders"),
        ("dev_pct", th.max_dev_pct, "DEV_HOLDING", "Dev wallet"),
        ("sniper_pct", th.max_sniper_pct, "SNIPER_HOLDING", "Snipers"),
        ("insider_pct", th.max_insider_pct, "INSIDER_HOLDING", "Insiders"),
        ("bundler_pct", th.max_bundler_pct, "BUNDLER_HOLDING", "Bundled wallets"),
        ("new_wallet_pct", th.max_new_wallet_pct, "NEW_WALLET_HOLDING", "Fresh wallets"),
    ):
        v = getattr(snap, field)
        if v is not None and v > limit:
            if v > limit * 2 and code in ("DEV_HOLDING", "BUNDLER_HOLDING"):
                add(code + "_EXTREME", "critical", f"{label} hold {v:.1f}% of supply (> 2x the {limit}% limit)")
            else:
                add(code, "warn", f"{label} hold {v:.1f}% of supply (limit {limit}%)")

    # Dev behaviour
    if snap.dev_sell_pct is not None and snap.dev_sell_pct >= th.dev_dump_pct and not snap.dev_sold_all:
        add("DEV_SELLING", "warn", f"Dev has sold {snap.dev_sell_pct:.0f}% of their allocation")
    if snap.dev_sold_all:
        add("DEV_EXITED", "info", "Dev sold their whole position (removes dev-dump risk, but also dev commitment)")
    if snap.dev_migrate_count is not None and snap.dev_migrate_count >= 5:
        add("SERIAL_LAUNCHER", "info", f"Dev has {snap.dev_migrate_count} previously migrated tokens — check their history")

    # Taxes (null = unknown, never 0)
    taxes = [t for t in (snap.tax_buy_pct, snap.tax_sell_pct) if t is not None]
    if taxes and max(taxes) > th.max_tax_pct:
        add("TAX_EXTREME", "critical", f"Trade tax up to {max(taxes):.1f}% (> {th.max_tax_pct}%)")
    elif taxes and max(taxes) > th.warn_tax_pct:
        add("TAX_HIGH", "warn", f"Trade tax up to {max(taxes):.1f}%")

    # Liquidity
    liq = snap.liquidity_usd
    if liq is None:
        add("LIQUIDITY_UNKNOWN", "warn", "Liquidity unknown — slippage and exit risk cannot be estimated")
    elif liq < th.min_liquidity_usd:
        add("LIQUIDITY_TOO_LOW", "critical", f"Liquidity ${liq:,.0f} below ${th.min_liquidity_usd:,.0f} minimum")
    ltm = feat.get("liq_to_mcap")
    if ltm is not None and ltm < th.min_liq_to_mcap:
        add("THIN_LIQUIDITY", "warn", f"Liquidity is only {ltm:.1%} of market cap — price is easy to move / dump")
    if snap.market_cap_usd is not None and snap.market_cap_usd > th.max_market_cap_usd:
        add("LARGE_CAP", "info", f"Market cap ${snap.market_cap_usd:,.0f} above early-stage range")

    # Smart money leaving
    er = feat.get("smart_money_exit_rate_pct")
    if er is not None and er >= 50:
        add("SMART_MONEY_EXITING", "warn", f"{er:.0f}% of signalling smart-money wallets already exited")
    if feat.get("smart_money_sell_signals"):
        add("SMART_MONEY_SELLS", "info", f"{feat['smart_money_sell_signals']} smart-money sell signal(s)")

    # Attention without substance
    if feat.get("social_links") == 0:
        add("NO_SOCIALS", "info", "No website / X / Telegram links reported")

    # Over-extension: chasing a vertical move
    p1h = feat.get("price_change_1h_pct")
    if p1h is not None and p1h > 300:
        add("OVEREXTENDED", "warn", f"Price +{p1h:.0f}% in 1h — late entry risk is high")

    # Stale data
    if feat.get("data_age_s") is not None and feat["data_age_s"] > th.stale_after_s:
        add("DATA_STALE", "critical", f"Latest data is {feat['data_age_s']:.0f}s old")
    return flags
