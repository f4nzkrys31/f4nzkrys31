"""Heatmaps: launchpad opportunity grid and exchange-listed market map."""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

AGE_BUCKETS = [("<15m", 15), ("15-60m", 60), ("1-6h", 360), ("6-24h", 1440), (">24h", float("inf"))]


def age_bucket(age_min: float | None) -> str:
    if age_min is None:
        return "unknown"
    for name, upper in AGE_BUCKETS:
        if age_min < upper:
            return name
    return ">24h"


def launchpad_heatmap(cands: list[dict[str, Any]], now: float | None = None) -> dict[str, Any]:
    """Rows = platform, columns = token age; cell = count, mean rank, total 24h volume, best token."""
    now = now or time.time()
    cells: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {"n": 0, "rank_sum": 0.0, "volume": 0.0,
                                                                         "blocked": 0, "best": None})
    for c in cands:
        snap = c.get("snapshot") or {}
        created = snap.get("created_at")
        age = (now - created) / 60 if created else None
        key = (c.get("platform") or "unknown", age_bucket(age))
        cell = cells[key]
        cell["n"] += 1
        cell["rank_sum"] += c.get("rank_score") or 0
        cell["volume"] += snap.get("volume_24h_usd") or 0
        cell["blocked"] += 1 if c.get("blocked") else 0
        if not c.get("blocked") and (cell["best"] is None or c["rank_score"] > cell["best"]["rank_score"]):
            cell["best"] = {"symbol": c.get("symbol"), "address": c["address"], "chain": c["chain"],
                            "rank_score": c["rank_score"]}
    rows = sorted({k[0] for k in cells})
    cols = [b for b, _ in AGE_BUCKETS] + (["unknown"] if any(k[1] == "unknown" for k in cells) else [])
    grid = []
    for r in rows:
        line = []
        for col in cols:
            cell = cells.get((r, col))
            line.append(None if not cell else {
                "n": cell["n"], "mean_rank": round(cell["rank_sum"] / cell["n"], 1), "volume_24h_usd": round(cell["volume"]),
                "blocked": cell["blocked"], "best": cell["best"]})
        grid.append({"platform": r, "cells": line})
    return {"columns": cols, "rows": grid}


def narrative_heatmap(topics: list[dict[str, Any]], limit: int = 30) -> list[dict[str, Any]]:
    out = [{"name": t.get("name"), "chain": t.get("chain"), "net_inflow_1h_usd": t.get("net_inflow_1h_usd"),
            "net_inflow_usd": t.get("net_inflow_usd"), "tokens": len(t.get("token_addresses") or []),
            "tags": t.get("tags") or []} for t in topics]
    out.sort(key=lambda x: x["net_inflow_1h_usd"] or 0, reverse=True)
    return out[:limit]
