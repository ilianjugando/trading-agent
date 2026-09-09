import json

from signals.crypto_fundamentals import (
    fetch_market_fundamentals,
    okx_inst_id_to_symbol,
)


class _FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _coin(symbol, rank, market_cap, fdv=None, circulating=None, total=None, volume=None):
    return {
        "symbol": symbol,
        "market_cap_rank": rank,
        "market_cap": market_cap,
        "fully_diluted_valuation": fdv,
        "circulating_supply": circulating,
        "total_supply": total,
        "total_volume": volume,
    }


def _patch_market_data(monkeypatch, rows):
    monkeypatch.setattr(
        "signals.crypto_fundamentals.urllib.request.urlopen",
        lambda req, timeout=20: _FakeResponse(rows),
    )


def test_size_bucket_thresholds(monkeypatch):
    rows = [
        _coin("big", 1, 15_000_000_000, circulating=100, total=100, volume=1_000_000_000),
        _coin("mid", 2, 5_000_000_000, circulating=100, total=100, volume=100_000_000),
        _coin("sml", 3, 500_000_000, circulating=100, total=100, volume=10_000_000),
        _coin("tny", 4, 50_000_000, circulating=100, total=100, volume=1_000_000),
        _coin("edge", 5, 10_000_000_000, circulating=100, total=100, volume=1),  # exact large boundary
        _coin("midedge", 6, 1_000_000_000, circulating=100, total=100, volume=1),  # exact mid boundary
        _coin("smledge", 7, 100_000_000, circulating=100, total=100, volume=1),  # exact small boundary
    ]
    _patch_market_data(monkeypatch, rows)

    out = fetch_market_fundamentals()
    assert out["BIG"].size_bucket == "large"
    assert out["MID"].size_bucket == "mid"
    assert out["SML"].size_bucket == "small"
    assert out["TNY"].size_bucket == "micro"
    assert out["EDGE"].size_bucket == "large"  # boundary is inclusive
    assert out["MIDEDGE"].size_bucket == "mid"  # boundary is inclusive
    assert out["SMLEDGE"].size_bucket == "small"  # boundary is inclusive


def test_ratio_calculations(monkeypatch):
    rows = [
        _coin("aaa", 1, market_cap=1_000_000_000, fdv=2_000_000_000,
              circulating=50_000_000, total=100_000_000, volume=250_000_000),
    ]
    _patch_market_data(monkeypatch, rows)

    fundamentals = fetch_market_fundamentals()["AAA"]
    assert fundamentals.market_cap_usd == 1_000_000_000.0
    assert fundamentals.fdv_usd == 2_000_000_000.0
    assert fundamentals.circulating_supply_pct == 50.0
    assert fundamentals.volume_to_mcap_ratio == 0.25
    assert fundamentals.rank == 1
    assert isinstance(fundamentals.market_cap_usd, float)
    assert isinstance(fundamentals.circulating_supply_pct, float)


def test_missing_optional_fields_become_none(monkeypatch):
    rows = [_coin("zzz", 500, market_cap=200_000_000, fdv=None, circulating=None, total=None, volume=None)]
    _patch_market_data(monkeypatch, rows)

    fundamentals = fetch_market_fundamentals()["ZZZ"]
    assert fundamentals.fdv_usd is None
    assert fundamentals.circulating_supply_pct is None
    assert fundamentals.volume_to_mcap_ratio is None


def test_rows_without_market_cap_are_skipped(monkeypatch):
    rows = [
        _coin("nomc", 999, market_cap=None),
        _coin("ok", 1, market_cap=1_000_000_000, circulating=1, total=1, volume=1),
    ]
    _patch_market_data(monkeypatch, rows)

    out = fetch_market_fundamentals()
    assert "NOMC" not in out
    assert "OK" in out


def test_ticker_collision_keeps_highest_market_cap(monkeypatch):
    # CoinGecko returns rows sorted market_cap_desc, and multiple unrelated
    # projects (or scam clones) can share a ticker -- the larger one, seen
    # first, must win.
    rows = [
        _coin("sui", 50, market_cap=5_000_000_000, circulating=1, total=1, volume=1),
        _coin("sui", 4000, market_cap=10_000, circulating=1, total=1, volume=1),
    ]
    _patch_market_data(monkeypatch, rows)

    out = fetch_market_fundamentals()
    assert len(out) == 1
    assert out["SUI"].market_cap_usd == 5_000_000_000.0
    assert out["SUI"].rank == 50


def test_okx_inst_id_to_symbol():
    assert okx_inst_id_to_symbol("BTC-USDT") == "BTC"
    assert okx_inst_id_to_symbol("eth-usdt") == "ETH"


def test_fetch_market_fundamentals_raises_on_network_failure(monkeypatch):
    def _boom(req, timeout=20):
        raise TimeoutError("network blew up")

    monkeypatch.setattr("signals.crypto_fundamentals.urllib.request.urlopen", _boom)

    try:
        fetch_market_fundamentals()
        assert False, "expected an exception, not a silently-swallowed empty dict"
    except TimeoutError:
        pass
