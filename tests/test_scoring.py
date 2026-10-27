from alpha_scanner.analytics.features import compute_features, window_rates
from alpha_scanner.analytics.filters import detect_flags
from alpha_scanner.config import ScoringWeights, Thresholds
from alpha_scanner.models import SecurityHit, SecurityReport, SecurityStatus, SmartMoneySignal, Stage, TokenSnapshot
from alpha_scanner.scoring.engine import score

NOW = 1_800_000_000.0
W, TH = ScoringWeights(), Thresholds()


def snap(**kw):
    base = dict(chain="CT_501", address="Mint1", ts=NOW, symbol="T", stage=Stage.BONDING, created_at=NOW - 1800,
                price_usd=0.00002, price_change_pct=60, market_cap_usd=200_000, liquidity_usd=40_000,
                volume_24h_usd=80_000, volume_5m_usd=6_000, volume_1h_usd=30_000, buys_5m=120, sells_5m=40,
                trades_5m=160, unique_traders_5m=80, unique_traders_1h=300, holders=900, top10_pct=20, dev_pct=2,
                socials={"twitter": "x", "website": "y"}, twitter_followers=4000)
    base.update(kw)
    return TokenSnapshot(**base)


def checked(**kw):
    return SecurityReport(chain="CT_501", address="Mint1", status=SecurityStatus.CHECKED, risk_level=1, **kw)


def run(s, sec=None, history=None, signals=None):
    f = compute_features(history or [s], signals, now=NOW)
    return score(s, f, sec, W, TH, now=NOW)


def test_window_rates_from_history():
    hist = [snap(ts=NOW - 600 + i * 60, holders=100 + i * 10) for i in range(11)]
    recent, prior = window_rates(hist, "holders", 300)
    assert round(recent * 60, 6) == 10 and round(prior * 60, 6) == 10


def test_provider_windows_drive_volume_accel():
    f = compute_features([snap()], now=NOW)
    assert f["volume_accel"] == round(6000 * 12 / 30000, 3)
    assert f["trades_per_trader_5m"] == 2.0 and f["buy_ratio"] == 0.75


def test_strong_clean_token_scores_well_and_is_not_blocked():
    c = run(snap(), checked())
    assert not c.blocked and c.opportunity_score > 40 and c.risk_score < 20
    assert c.why_detected and c.invalidation and any("Heuristic" in r for r in c.remaining_risks)


def test_unverified_security_is_flagged_not_safe():
    c_checked, c_unknown = run(snap(), checked()), run(snap(), None)
    assert any(f.code == "SECURITY_UNVERIFIED" for f in c_unknown.flags)
    assert c_unknown.risk_score >= c_checked.risk_score + 15
    # unknown is penalised at least like a medium (level-3) audit result
    medium = run(snap(), SecurityReport(chain="CT_501", address="Mint1", status=SecurityStatus.CHECKED, risk_level=3))
    assert c_unknown.risk_score >= medium.risk_score
    for status in (SecurityStatus.UNSUPPORTED, SecurityStatus.UNAVAILABLE):
        c = run(snap(), SecurityReport(chain="CT_501", address="Mint1", status=status))
        assert c.security_status == status and any(f.code == "SECURITY_UNVERIFIED" for f in c.flags)


def test_honeypot_blocks():
    c = run(snap(), checked(hits=[SecurityHit(category="CONTRACT_RISK", title="Honeypot Risk Found", severity="RISK")]))
    assert c.blocked and c.risk_score >= 90


def test_high_audit_risk_and_tax_block():
    assert run(snap(), SecurityReport(chain="CT_501", address="Mint1", status=SecurityStatus.CHECKED, risk_level=4)).blocked
    assert run(snap(tax_sell_pct=15), checked()).blocked


def test_missing_data_lowers_confidence_and_score():
    full = run(snap(), checked())
    sparse = run(snap(volume_5m_usd=None, volume_1h_usd=None, unique_traders_5m=None, buys_5m=None, sells_5m=None,
                      trades_5m=None, price_change_pct=None, holders=None), checked())
    assert sparse.confidence < full.confidence and sparse.opportunity_score < full.opportunity_score
    assert any("Missing data" in r for r in sparse.remaining_risks)


def test_more_unique_buyers_increases_score():
    lo = run(snap(unique_traders_5m=10, trades_5m=20), checked())
    hi = run(snap(unique_traders_5m=150, trades_5m=300), checked())
    assert hi.opportunity_score > lo.opportunity_score


def test_smart_money_component_and_exit_penalty():
    sig = lambda er: SmartMoneySignal(chain="CT_501", address="Mint1", signal_id="1", direction="buy",  # noqa: E731
                                      smart_money_count=5, exit_rate_pct=er, status="active")
    fresh, exited = run(snap(), checked(), signals=[sig(0)]), run(snap(), checked(), signals=[sig(90)])
    sm = lambda c: next(x for x in c.components if x.name == "smart_money").value  # noqa: E731
    assert sm(fresh) > sm(exited)
    assert any(f.code == "SMART_MONEY_EXITING" for f in exited.flags)


def test_low_liquidity_blocks_and_unknown_liquidity_warns():
    assert run(snap(liquidity_usd=1_000), checked()).blocked
    c = run(snap(liquidity_usd=None), checked())
    assert any(f.code == "LIQUIDITY_UNKNOWN" for f in c.flags) and not c.blocked


def test_stale_data_blocks():
    s = snap(ts=NOW - 3600)
    f = compute_features([s], now=NOW)
    assert score(s, f, checked(), W, TH, now=NOW).blocked


def test_manipulation_flags():
    codes = lambda s: {f.code for f in detect_flags(s, compute_features([s], now=NOW), TH)}  # noqa: E731
    assert "BOT_CHURN" in codes(snap(trades_5m=900, unique_traders_5m=40))
    assert "ONE_SIDED_FLOW" in codes(snap(buys_5m=200, sells_5m=2, unique_traders_5m=10, trades_5m=20))
    assert "VOLUME_HOLDER_MISMATCH" in codes(snap(volume_24h_usd=900_000, holders=60))
    assert "WASH_TAGGED" in codes(snap(tag_dev_wash=True))
    assert "DEV_HOLDING_EXTREME" in codes(snap(dev_pct=30))
    assert "TOP10_CONCENTRATION" in codes(snap(top10_pct=60))
    assert "OVEREXTENDED" not in codes(snap())


def test_score_bounds():
    for s in (snap(), snap(unique_traders_5m=10_000, holders=10**6, price_change_pct=10_000, market_cap_usd=1)):
        c = run(s, checked())
        assert 0 <= c.opportunity_score <= 100 and 0 <= c.risk_score <= 100 and 0 <= c.rank_score <= 100
