import httpx
import pytest
import respx

from alpha_scanner.ingestion.binance_web3 import MEME_RUSH_URL, BinanceWeb3Source
from alpha_scanner.ingestion.http import CircuitOpen, SourceClient, SourceError

URL = "https://example.test/x"


def client(**kw):
    return SourceClient("t", rate_per_min=10_000, backoff_base_s=0.001, **kw)


@respx.mock
async def test_retries_then_succeeds():
    route = respx.get(URL).mock(side_effect=[httpx.Response(500), httpx.Response(429), httpx.Response(200, json={"ok": 1})])
    assert await client().get(URL) == {"ok": 1}
    assert route.call_count == 3


@respx.mock
async def test_client_error_not_retried():
    route = respx.get(URL).mock(return_value=httpx.Response(404))
    with pytest.raises(SourceError):
        await client().get(URL)
    assert route.call_count == 1


@respx.mock
async def test_circuit_opens_after_threshold():
    respx.get(URL).mock(side_effect=httpx.ConnectError("down"))
    c = client(retries=0, breaker_threshold=2, breaker_cooldown_s=60)
    for _ in range(2):
        with pytest.raises(SourceError):
            await c.get(URL)
    assert c.health.circuit_open
    with pytest.raises(CircuitOpen):
        await c.get(URL)


@respx.mock
async def test_business_error_code_raises_and_is_recorded():
    respx.post(MEME_RUSH_URL).mock(return_value=httpx.Response(200, json={"code": "100004", "success": False}))
    src = BinanceWeb3Source()
    with pytest.raises(SourceError):
        await src.meme_rush("CT_501", 10)
    assert "100004" in src.http.health.last_error


@respx.mock
async def test_meme_rush_request_shape():
    route = respx.post(MEME_RUSH_URL).mock(return_value=httpx.Response(200, json={"code": "000000", "data": [
        {"chainId": "CT_501", "contractAddress": "Mint1", "protocol": 1001}]}))
    out = await BinanceWeb3Source().meme_rush("CT_501", 10, limit=500)
    sent = route.calls[0].request
    assert b'"limit": 100' in sent.content or b'"limit":100' in sent.content  # capped per docs
    assert sent.headers["user-agent"].startswith("binance-web3/")
    assert out[0].platform == "Pump.fun"
