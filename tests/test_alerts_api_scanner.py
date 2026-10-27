import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from alpha_scanner.alerts.engine import AlertEngine, TelegramSink, format_alert, should_alert
from alpha_scanner.api.server import create_app
from alpha_scanner.config import AlertConfig, Settings, load_settings
from alpha_scanner.ingestion.demo import DemoDex, DemoMarket, DemoSpot, DemoWeb3, SimClock
from alpha_scanner.ingestion.http import SourceError
from alpha_scanner.models import SecurityStatus, Stage
from alpha_scanner.scanner import Scanner
from tests.test_scoring import checked, run, snap

CFG = AlertConfig(min_score=10, max_risk=60, min_confidence=0.3, cooldown_s=600, rescore_jump=10)


def test_should_alert_rules():
    good = run(snap(), checked())
    assert should_alert(good, CFG, None, False, now=0) == "new_candidate"
    assert should_alert(good, CFG, {"ts": 0, "score": good.rank_score}, False, now=100) is None  # cooldown
    assert should_alert(good, CFG, {"ts": 0, "score": good.rank_score}, False, now=10_000) is None  # no jump
    assert should_alert(good, CFG, {"ts": 0, "score": good.rank_score - 20}, False, now=10_000) == "score_jump"
    blocked = run(snap(liquidity_usd=100), checked())
    assert should_alert(blocked, CFG.model_copy(update={"max_risk": 100}), None, False) is None


def test_alert_text_escapes_token_strings():
    c = run(snap(symbol="<b>x</b><script>", name="&evil"), checked())
    text = format_alert(c, "new_candidate")
    assert "<script>" not in text and "&lt;script&gt;" in text and "No trade was placed" in text


@respx.mock
async def test_telegram_delivery_and_dedupe(store):
    route = respx.post(url__regex=r"https://api\.telegram\.org/bot.*/sendMessage").mock(return_value=httpx.Response(200))
    eng = AlertEngine(store, CFG, TelegramSink("123:abc", "42"))
    c = run(snap(), checked())
    fired = await eng.process([c], now=1000)
    assert fired and route.called and store.alerts()[0]["delivered"]["telegram"] is True
    assert await eng.process([c], now=1100) == []  # cooldown dedupe


# ---------- API ----------
@pytest.fixture
def api(store, settings):
    store.save_candidate(run(snap(), checked()))
    return TestClient(create_app(settings, store), base_url="http://127.0.0.1:8800")


def _token(api):
    html = api.get("/").text
    return html.split('name="alpha-token" content="')[1].split('"')[0]


def test_api_host_and_token_guards(api):
    assert api.get("/api/health").status_code == 200
    assert api.get("/api/health", headers={"host": "evil.example"}).status_code == 403
    body = {"chain": "CT_501", "address": "Mint1111111111111111111111111111111"}
    assert api.post("/api/watchlist", json=body).status_code == 403
    assert api.post("/api/watchlist", json=body, headers={"x-alpha-token": _token(api),
                                                          "origin": "http://evil.example"}).status_code == 403
    assert api.post("/api/watchlist", json=body, headers={"x-alpha-token": _token(api)}).status_code == 200
    assert api.get("/api/watchlist").json()[0]["address"] == body["address"]


def test_api_headers_and_validation(api):
    r = api.get("/api/candidates")
    assert "script-src 'self'" in r.headers["content-security-policy"] and r.headers["x-frame-options"] == "DENY"
    assert r.json()[0]["symbol"] == "T"
    assert api.get("/api/candidates?min_rank=101").json() == []
    assert api.get("/api/token/CT_501/not-an-address!").status_code in (400, 404)
    assert api.get("/api/token/999/Mint1").status_code == 400
    assert api.get("/api/heatmap").json()["launchpad"]["rows"]


def test_config_refuses_remote_bind_and_reads_env(tmp_path, caplog):
    with pytest.raises(ValueError):
        Settings(host="0.0.0.0")
    s = load_settings(env={"ALPHA_CHAINS": "56,8453", "ALPHA_PORT": "9000", "BINANCE_API_SECRET": "x"})
    assert s.sources.chains == ["56", "8453"] and s.port == 9000
    assert "BINANCE_API_SECRET" in caplog.text and "x" not in caplog.text.replace("BINANCE_API_SECRET", "")
    with pytest.raises(ValueError):
        load_settings(env={"ALPHA_CHAINS": "1"})


# ---------- scanner end-to-end on the synthetic market ----------
async def test_scanner_demo_end_to_end(store):
    settings = Settings(db_url="sqlite:///:memory:")
    clock = SimClock(1_800_000_000)
    m = DemoMarket(clock=clock)
    sc = Scanner(settings, store, DemoWeb3(m), DemoDex(m), DemoSpot(), alerts=AlertEngine(store, CFG), clock=clock)
    for _ in range(9):
        cands = await sc.run_cycle()
        clock.advance(60)
    by_kind = {t.address: t.kind for t in m.tokens}
    res = {c.address: c for c in cands}
    assert len(res) == len(m.tokens)
    for addr, kind in by_kind.items():
        c = res[addr]
        if kind == "honeypot":
            assert c.blocked and any(f.code == "AUDIT_HONEYPOT" for f in c.flags)
        if kind == "rug":
            assert c.blocked  # LP pulled + dev holding
        if kind == "late":
            assert c.security_status == SecurityStatus.UNSUPPORTED
    clean = [c for c in cands if not c.blocked]
    assert clean and max(c.rank_score for c in clean) > max(c.rank_score for c in cands if c.blocked)
    assert len(store.history(cands[0].chain, cands[0].address, 0)) == 9
    assert sc.spot_heatmap and store.alerts()


class FailingWeb3:
    async def meme_rush(self, *a, **k):
        raise SourceError("down")

    async def topic_rush(self, *a, **k):
        raise SourceError("down")

    async def smart_money(self, *a, **k):
        return []

    async def token_audit(self, *a, **k):
        raise AssertionError("no tokens -> no audits")


async def test_scanner_survives_source_outage(store):
    sc = Scanner(Settings(db_url="sqlite:///:memory:"), store, FailingWeb3(), None, None)
    assert await sc.run_cycle() == []
    assert any("meme-rush" in e for e in sc.status["last_errors"])
    assert store.last_cycle()["errors"]


def test_snapshot_merge_prefers_known_values():
    a = snap(liquidity_usd=None, sources=["a"], stage=Stage.UNKNOWN)
    b = snap(liquidity_usd=5, holders=1, sources=["b"], stage=Stage.MIGRATED)
    m = a.merge(b)
    assert m.liquidity_usd == 5 and m.holders == 900 and m.sources == ["a", "b"] and m.stage == Stage.MIGRATED
