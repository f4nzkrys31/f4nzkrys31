"""Parsers against the response shapes documented in binance-skills-hub and DexScreener."""

from alpha_scanner.ingestion.binance_web3 import parse_audit, parse_meme_rush_row, parse_smart_money, parse_topic
from alpha_scanner.ingestion.dexscreener import aggregate_pairs
from alpha_scanner.models import SecurityStatus, Stage

PUMP_ROW = {
    "chainId": "CT_501", "contractAddress": "AbCdEfGh1234567890abcdefghijkLMNOPqrstuvpump", "symbol": "TEST",
    "name": "Test", "price": "0.0000123", "priceChange": "42.5", "marketCap": "12300", "liquidity": "8000",
    "volume": "55000", "netBuy": "1200", "holders": 321, "progress": "37.5", "protocol": 1001,
    "countBuy": 900, "countSell": 400, "holdersTop10Percent": "22.1", "holdersDevPercent": "3.5",
    "holdersSniperPercent": None, "devPosition": None, "taxRateBuy": None, "taxRateSell": None,
    "tagDevWashTrading": None, "tagInsiderWashTrading": 1, "migrateStatus": 0, "createTime": 1_790_000_000_000,
    "socials": {"twitter": "https://x.com/test", "website": ""}, "twitterInfo": {"followersCnt": 1500},
}


def test_meme_rush_row_preserves_unknowns():
    s = parse_meme_rush_row(PUMP_ROW, 10)
    assert s.platform == "Pump.fun" and s.stage == Stage.BONDING
    assert s.address == PUMP_ROW["contractAddress"]  # Solana addresses keep their case
    assert s.price_usd == 0.0000123 and s.progress_pct == 37.5 and s.holders == 321
    assert s.sniper_pct is None and s.tax_buy_pct is None  # null -> unknown, never 0
    assert s.dev_sold_all is None
    assert s.tag_dev_wash is None and s.tag_insider_wash is True
    assert s.created_at == 1_790_000_000
    assert s.socials == {"twitter": "https://x.com/test"}
    assert s.twitter_followers == 1500


def test_meme_rush_evm_address_lowercased_and_migrated():
    row = {**PUMP_ROW, "chainId": "56", "contractAddress": "0xABCDEFabcdef0123456789abcdef0123456789AB",
           "protocol": 2001, "migrateStatus": 1, "devPosition": 2}
    s = parse_meme_rush_row(row, 10)
    assert s.address == row["contractAddress"].lower()
    assert s.stage == Stage.MIGRATED and s.platform == "Four.meme" and s.dev_sold_all is True


def test_meme_rush_garbage_numbers_become_none():
    s = parse_meme_rush_row({**PUMP_ROW, "price": "NaN", "marketCap": "abc", "holders": ""}, 20)
    assert s.price_usd is None and s.market_cap_usd is None and s.holders is None
    assert s.stage == Stage.FINALIZING
    assert parse_meme_rush_row({"symbol": "x"}, 10) is None


def test_audit_requires_both_flags():
    body = {"code": "000000", "success": True, "data": {"hasResult": True, "isSupported": False, "riskLevel": 1}}
    r = parse_audit("56", "0xabc", body)
    assert r.status == SecurityStatus.UNSUPPORTED and r.risk_level is None


def test_audit_error_envelope_is_unavailable():
    r = parse_audit("56", "0xabc", {"code": "100004", "success": False, "message": "rate limited"})
    assert r.status == SecurityStatus.UNAVAILABLE and "100004" in r.error


def test_audit_hits_parsed():
    body = {"code": "000000", "success": True, "data": {
        "hasResult": True, "isSupported": True, "riskLevelEnum": "HIGH", "riskLevel": 4,
        "extraInfo": {"buyTax": "1", "sellTax": "12", "isVerified": False},
        "riskItems": [{"id": "CONTRACT_RISK", "details": [
            {"title": "Honeypot Risk Found", "isHit": True, "riskType": "RISK"},
            {"title": "Mint function", "isHit": False, "riskType": "CAUTION"}]}]}}
    r = parse_audit("56", "0xabc", body)
    assert r.status == SecurityStatus.CHECKED and r.risk_level == 4 and r.sell_tax_pct == 12 and r.verified is False
    assert [h.title for h in r.hits] == ["Honeypot Risk Found"]


def test_smart_money_max_gain_is_fraction():
    s = parse_smart_money({"signalId": 9, "chainId": "56", "contractAddress": "0xAB", "direction": "BUY",
                           "smartMoneyCount": 4, "maxGain": "0.25", "exitRate": 30, "signalTriggerTime": 1_000_000})
    assert s.max_gain_pct == 25 and s.direction == "buy" and s.address == "0xab" and s.trigger_time == 1000


def test_topic_parse_links_tokens():
    topic, snaps = parse_topic({"chainId": "CT_501", "topicId": 7, "name": {"topicNameEn": "AI agents"},
                                "topicNetInflow1h": "5000", "tokenList": [
                                    {"contractAddress": "Mint111", "symbol": "A", "uniqueTrader5m": 12,
                                     "uniqueTrader1h": 40, "internal": 1}]})
    assert topic.name == "AI agents" and topic.token_addresses == ["Mint111"] and topic.net_inflow_1h_usd == 5000
    assert snaps[0].unique_traders_5m == 12 and snaps[0].topics == ["AI agents"] and snaps[0].stage == Stage.BONDING


def test_dexscreener_aggregates_only_base_token_pools():
    addr = "0xtoken"
    pairs = [
        {"baseToken": {"address": "0xTOKEN", "symbol": "T"}, "priceUsd": "1.5", "liquidity": {"usd": 30000},
         "volume": {"m5": 100, "h1": 1200, "h24": 9000}, "txns": {"m5": {"buys": 5, "sells": 2}, "h1": {"buys": 50, "sells": 20}},
         "marketCap": 1_000_000, "pairCreatedAt": 1_700_000_000_000},
        {"baseToken": {"address": "0xtoken"}, "liquidity": {"usd": 10000}, "volume": {"m5": 50, "h1": 300},
         "txns": {"m5": {"buys": 1, "sells": 1}}, "pairCreatedAt": 1_600_000_000_000},
        {"baseToken": {"address": "0xother"}, "liquidity": {"usd": 999999}},
    ]
    s = aggregate_pairs("56", addr, pairs)
    assert s.liquidity_usd == 40000 and s.volume_5m_usd == 150 and s.volume_1h_usd == 1500
    assert s.buys_5m == 6 and s.sells_5m == 3 and s.trades_5m == 9 and s.trades_1h == 70
    assert s.price_usd == 1.5 and s.created_at == 1_600_000_000
    assert aggregate_pairs("56", "0xnone", pairs) is None
