"""Alert rules, cooldown/dedupe, and delivery (log + optional Telegram).

Alerts are informational. They never trigger trades.
"""

from __future__ import annotations

import html
import logging
import time
from typing import Any

import httpx

from ..config import AlertConfig
from ..models import Candidate
from ..storage.db import Store

log = logging.getLogger(__name__)

EXPLORERS = {"CT_501": "https://solscan.io/token/{}", "56": "https://bscscan.com/token/{}",
             "8453": "https://basescan.org/token/{}"}


def should_alert(c: Candidate, cfg: AlertConfig, last: dict[str, Any] | None, watched: bool,
                 now: float | None = None) -> str | None:
    """Return alert kind or None."""
    now = now or time.time()
    if c.blocked:
        return None
    min_score = cfg.min_score - (10 if watched else 0)
    if c.rank_score < min_score or c.risk_score > cfg.max_risk or c.confidence < cfg.min_confidence:
        return None
    if last is None:
        return "watchlist" if watched else "new_candidate"
    if now - last["ts"] < cfg.cooldown_s:
        return None
    if c.rank_score >= (last.get("score") or 0) + cfg.rescore_jump:
        return "score_jump"
    return None


def format_alert(c: Candidate, kind: str) -> str:
    e = html.escape  # token names/symbols are attacker-controlled
    title = {"new_candidate": "New candidate", "score_jump": "Score jump", "watchlist": "Watchlist"}.get(kind, kind)
    lines = [
        f"<b>{e(title)}: {e(c.symbol or '?')}</b> ({e(c.platform or c.chain)}, {e(c.stage.value)})",
        f"<code>{e(c.address)}</code>",
        f"Rank {c.rank_score:.0f} · Opportunity {c.opportunity_score:.0f} · Risk {c.risk_score:.0f} · "
        f"Confidence {c.confidence:.0%} · Security {e(c.security_status.value)}",
        "<b>Why:</b> " + e("; ".join(c.why_detected) or "-"),
    ]
    risks = [r for r in c.remaining_risks if not r.startswith("Heuristic")][:4]
    if risks:
        lines.append("<b>Risks:</b> " + e("; ".join(risks)))
    lines.append("<b>Invalidated if:</b> " + e("; ".join(c.invalidation[:3])))
    if url := EXPLORERS.get(c.chain):
        lines.append(url.format(e(c.address)))
    lines.append("<i>Heuristic signal, not advice. No trade was placed.</i>")
    return "\n".join(lines)


class TelegramSink:
    def __init__(self, token: str, chat_id: str, client: httpx.AsyncClient | None = None) -> None:
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.chat_id = chat_id
        self._client = client or httpx.AsyncClient(timeout=10)

    async def send(self, text: str) -> bool:
        try:
            r = await self._client.post(self._url, json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                                                         "disable_web_page_preview": True})
            return r.status_code == 200
        except httpx.HTTPError as e:
            # Never log the URL: it contains the bot token.
            log.warning("telegram delivery failed: %s", type(e).__name__)
            return False

    async def aclose(self) -> None:
        await self._client.aclose()


class AlertEngine:
    def __init__(self, store: Store, cfg: AlertConfig, telegram: TelegramSink | None = None) -> None:
        self.store, self.cfg = store, cfg
        self.telegram = telegram
        if telegram is None and cfg.telegram_bot_token and cfg.telegram_chat_id:
            self.telegram = TelegramSink(cfg.telegram_bot_token, cfg.telegram_chat_id)

    async def process(self, cands: list[Candidate], now: float | None = None) -> list[dict[str, Any]]:
        fired = []
        now = now or time.time()
        for c in cands:
            kind = should_alert(c, self.cfg, self.store.last_alert(c.chain, c.address),
                                self.store.is_watched(c.chain, c.address), now=now)
            if not kind:
                continue
            text = format_alert(c, kind)
            delivered = {"log": True}
            log.info("ALERT %s %s %s rank=%.0f risk=%.0f", kind, c.chain, c.symbol, c.rank_score, c.risk_score)
            if self.telegram:
                delivered["telegram"] = await self.telegram.send(text)
            self.store.add_alert(c.chain, c.address, kind, c.rank_score, text, delivered, now=now)
            fired.append({"kind": kind, "chain": c.chain, "address": c.address, "score": c.rank_score})
        return fired

    async def aclose(self) -> None:
        if self.telegram:
            await self.telegram.aclose()
