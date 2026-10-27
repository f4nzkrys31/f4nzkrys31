"""Shared HTTP layer: per-source rate limit, retries with backoff, circuit breaker.

Every external source goes through `SourceClient` so that failures are visible
on the health endpoint instead of silently producing empty data.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger(__name__)


class SourceError(Exception):
    """A source failed after retries (or its circuit is open)."""


class CircuitOpen(SourceError):
    pass


@dataclass
class SourceHealth:
    name: str
    calls: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    last_ok: float | None = None
    last_error: str | None = None
    last_error_at: float | None = None
    open_until: float = 0.0

    @property
    def circuit_open(self) -> bool:
        return time.time() < self.open_until

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "calls": self.calls, "failures": self.failures,
            "consecutive_failures": self.consecutive_failures, "last_ok": self.last_ok,
            "last_error": self.last_error, "last_error_at": self.last_error_at,
            "circuit_open": self.circuit_open, "open_until": self.open_until or None,
        }


HEALTH: dict[str, SourceHealth] = {}


@dataclass
class RateLimiter:
    """Token bucket: `rate` requests per `per` seconds."""

    rate: float
    per: float = 60.0
    _tokens: float = field(init=False)
    _updated: float = field(init=False)
    _lock: asyncio.Lock = field(init=False, default_factory=asyncio.Lock)

    def __post_init__(self) -> None:
        self._tokens = self.rate
        self._updated = time.monotonic()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(self.rate, self._tokens + (now - self._updated) * self.rate / self.per)
                self._updated = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                await asyncio.sleep((1 - self._tokens) * self.per / self.rate)


class SourceClient:
    def __init__(
        self,
        name: str,
        *,
        rate_per_min: float,
        timeout_s: float = 10.0,
        retries: int = 3,
        breaker_threshold: int = 5,
        breaker_cooldown_s: float = 120.0,
        headers: dict[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
        backoff_base_s: float = 0.5,
    ) -> None:
        self.name = name
        self.limiter = RateLimiter(rate_per_min)
        self.retries = retries
        self.breaker_threshold = breaker_threshold
        self.breaker_cooldown_s = breaker_cooldown_s
        self.backoff_base_s = backoff_base_s
        self.health = HEALTH.setdefault(name, SourceHealth(name))
        self._client = client or httpx.AsyncClient(timeout=timeout_s, headers=headers or {})
        self._extra_headers = headers or {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(self, method: str, url: str, **kw: Any) -> Any:
        if self.health.circuit_open:
            raise CircuitOpen(f"{self.name}: circuit open until {self.health.open_until:.0f}")
        headers = {**self._extra_headers, **kw.pop("headers", {})}
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            await self.limiter.acquire()
            self.health.calls += 1
            try:
                resp = await self._client.request(method, url, headers=headers, **kw)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request, response=resp)
                resp.raise_for_status()
                data = resp.json()
                self._ok()
                return data
            except (httpx.HTTPError, ValueError) as e:
                last = e
                status = getattr(getattr(e, "response", None), "status_code", None)
                retryable = status is None or status == 429 or status >= 500
                if not retryable or attempt == self.retries:
                    break
                delay = self.backoff_base_s * (2 ** attempt) + random.uniform(0, self.backoff_base_s)
                if status == 429:
                    retry_after = e.response.headers.get("retry-after")
                    if retry_after and retry_after.isdigit():
                        delay = max(delay, float(retry_after))
                await asyncio.sleep(delay)
        self._fail(last)
        raise SourceError(f"{self.name}: {last}") from last

    async def get(self, url: str, **kw: Any) -> Any:
        return await self.request("GET", url, **kw)

    async def post(self, url: str, **kw: Any) -> Any:
        return await self.request("POST", url, **kw)

    def _ok(self) -> None:
        self.health.consecutive_failures = 0
        self.health.last_ok = time.time()

    def _fail(self, e: Exception | None) -> None:
        h = self.health
        h.failures += 1
        h.consecutive_failures += 1
        h.last_error = str(e)[:300]
        h.last_error_at = time.time()
        if h.consecutive_failures >= self.breaker_threshold:
            h.open_until = time.time() + self.breaker_cooldown_s
            log.warning("%s: circuit opened for %.0fs after %d failures", self.name, self.breaker_cooldown_s,
                        h.consecutive_failures)

    def record_business_error(self, msg: str) -> None:
        """For APIs that return HTTP 200 with an error envelope."""
        self._fail(SourceError(msg))


def fnum(v: Any) -> float | None:
    """Parse a string/number field; empty, null and garbage become None (unknown)."""
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def inum(v: Any) -> int | None:
    f = fnum(v)
    return int(f) if f is not None else None
