"""Configuration: YAML file + ALPHA_* environment overrides.

Every threshold the scanner uses lives here so that strategy changes are
config diffs, not code diffs (and can be recorded alongside backtests).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

log = logging.getLogger(__name__)

# Chains the Binance Web3 market endpoints accept (verified in binance-skills-hub
# meme-rush / trading-signal / query-token-audit skill docs).
SUPPORTED_CHAINS = {"CT_501": "solana", "56": "bsc", "8453": "base"}

# Names of credentials that belong to the (future) execution layer. The scanner
# never reads them; if they are present in its environment we warn, because the
# scanner process should run without access to trading secrets.
TRADING_SECRET_ENV_NAMES = (
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
    "BINANCE_SECRET_KEY",
    "PRIVATE_KEY",
    "WALLET_PRIVATE_KEY",
    "SEED_PHRASE",
    "MNEMONIC",
    "BINANCE_BAW_DIR",
)


class ScoringWeights(BaseModel):
    """Relative weights of opportunity components (normalised at runtime)."""

    momentum: float = 15
    volume_acceleration: float = 15
    unique_buyers: float = 15
    holder_growth: float = 10
    smart_money: float = 15
    liquidity: float = 10
    attention: float = 10
    upside: float = 10


class Thresholds(BaseModel):
    # Liquidity / size filters
    min_liquidity_usd: float = 5_000
    min_liq_to_mcap: float = 0.03
    max_market_cap_usd: float = 50_000_000
    # Concentration limits (percent of supply)
    max_top10_pct: float = 45
    max_dev_pct: float = 10
    max_sniper_pct: float = 15
    max_insider_pct: float = 15
    max_bundler_pct: float = 15
    max_new_wallet_pct: float = 35
    # Taxes (percent)
    warn_tax_pct: float = 5
    max_tax_pct: float = 10
    # Manipulation heuristics
    max_trades_per_trader_5m: float = 8
    max_volume_per_holder_usd: float = 2_000
    one_sided_buy_ratio: float = 0.95
    one_sided_min_traders: int = 25
    dev_dump_pct: float = 50
    # Data freshness
    stale_after_s: int = 300
    # Security re-check interval
    audit_ttl_s: int = 1_800


class AlertConfig(BaseModel):
    min_score: float = 65
    max_risk: float = 45
    min_confidence: float = 0.5
    cooldown_s: int = 1_800
    rescore_jump: float = 15  # re-alert if score rises this much after cooldown
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None


class SourceConfig(BaseModel):
    chains: list[str] = Field(default_factory=lambda: ["CT_501", "56"])
    poll_interval_s: int = 60
    meme_rush_limit: int = 100
    meme_rush_rank_types: list[int] = Field(default_factory=lambda: [10, 20, 30])
    binance_web3_enabled: bool = True
    dexscreener_enabled: bool = True
    binance_spot_enabled: bool = True
    dexscreener_enrich_top_n: int = 60
    audit_top_n: int = 25
    spot_heatmap_top_n: int = 60
    http_timeout_s: float = 10.0

    @field_validator("chains")
    @classmethod
    def _chains_known(cls, v: list[str]) -> list[str]:
        bad = [c for c in v if c not in SUPPORTED_CHAINS]
        if bad:
            raise ValueError(f"unsupported chain ids {bad}; supported: {sorted(SUPPORTED_CHAINS)}")
        return v


class Settings(BaseModel):
    db_url: str = "sqlite:///data/alpha.db"
    host: str = "127.0.0.1"
    port: int = 8800
    demo_mode: bool = False
    history_window_s: int = 7_200
    # Raw snapshots are the Phase 2 backtest dataset; keep them long enough to test on.
    snapshot_retention_days: float = 30
    weights: ScoringWeights = Field(default_factory=ScoringWeights)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    alerts: AlertConfig = Field(default_factory=AlertConfig)
    sources: SourceConfig = Field(default_factory=SourceConfig)

    @field_validator("host")
    @classmethod
    def _localhost_only(cls, v: str) -> str:
        # The dashboard exposes watchlist writes; keep it off the network unless
        # an operator deliberately puts an authenticating proxy in front.
        if v not in ("127.0.0.1", "localhost", "::1") and os.environ.get("ALPHA_ALLOW_REMOTE_BIND") != "1":
            raise ValueError("host must be loopback (set ALPHA_ALLOW_REMOTE_BIND=1 to override, behind an auth proxy)")
        return v


_ENV_MAP: dict[str, tuple[str, ...]] = {
    "ALPHA_DB_URL": ("db_url",),
    "ALPHA_HOST": ("host",),
    "ALPHA_PORT": ("port",),
    "ALPHA_DEMO": ("demo_mode",),
    "ALPHA_CHAINS": ("sources", "chains"),
    "ALPHA_POLL_INTERVAL_S": ("sources", "poll_interval_s"),
    "TG_BOT_TOKEN": ("alerts", "telegram_bot_token"),
    "TG_CHAT_ID": ("alerts", "telegram_chat_id"),
}


def _set_path(d: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    for key in path[:-1]:
        d = d.setdefault(key, {})
    d[path[-1]] = value


def load_settings(path: str | Path | None = None, env: dict[str, str] | None = None) -> Settings:
    env = dict(os.environ if env is None else env)
    data: dict[str, Any] = {}
    path = path or env.get("ALPHA_CONFIG")
    if path and Path(path).exists():
        data = yaml.safe_load(Path(path).read_text()) or {}
    for name, target in _ENV_MAP.items():
        raw = env.get(name)
        if raw in (None, ""):
            continue
        value: Any = raw
        if target == ("sources", "chains"):
            value = [c.strip() for c in raw.split(",") if c.strip()]
        elif target == ("demo_mode",):
            value = raw.lower() in ("1", "true", "yes")
        _set_path(data, target, value)
    settings = Settings.model_validate(data)
    present = [n for n in TRADING_SECRET_ENV_NAMES if env.get(n)]
    if present:
        log.warning("trading credentials present in scanner environment (%s); the scanner does not use them — "
                    "run it without them", ", ".join(present))
    return settings
