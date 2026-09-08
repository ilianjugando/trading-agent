from signals.crypto_trend import liquid_universe, rank_universe


class _FakeOKX:
    def __init__(self, tickers=None, changes=None):
        self._tickers = tickers or []
        self._changes = changes or {}

    def get_spot_tickers(self):
        return self._tickers

    def get_24h_change_pct(self, inst_id):
        return self._changes[inst_id]


def _ticker(inst_id, volume, change=0.0):
    return {"inst_id": inst_id, "volume_24h_usd": volume, "change_24h_pct": change}


def test_liquid_universe_ranks_by_volume():
    okx = _FakeOKX(tickers=[
        _ticker("AAA-USDT", 10_000_000),
        _ticker("BBB-USDT", 90_000_000),
        _ticker("CCC-USDT", 50_000_000),
    ])
    assert liquid_universe(okx, top_n=2) == ["BBB-USDT", "CCC-USDT"]


def test_liquid_universe_drops_thin_pairs():
    okx = _FakeOKX(tickers=[
        _ticker("BIG-USDT", 90_000_000),
        _ticker("THIN-USDT", 1_000),
    ])
    assert liquid_universe(okx, min_volume_usd=5_000_000) == ["BIG-USDT"]


def test_liquid_universe_excludes_stablecoin_pairs():
    okx = _FakeOKX(tickers=[
        _ticker("USDC-USDT", 500_000_000),
        _ticker("BTC-USDT", 90_000_000),
    ])
    assert liquid_universe(okx) == ["BTC-USDT"]


def test_rank_universe_filters_below_threshold():
    okx = _FakeOKX(changes={"AAA-USDT": 0.005, "BBB-USDT": 0.08})
    ranked = rank_universe(okx, universe=["AAA-USDT", "BBB-USDT"], min_change_pct=3.0)
    assert [s.inst_id for s in ranked] == ["BBB-USDT"]


def test_rank_universe_empty_when_market_is_flat():
    okx = _FakeOKX(changes={"AAA-USDT": 0.005, "BBB-USDT": -0.01})
    assert rank_universe(okx, universe=["AAA-USDT", "BBB-USDT"], min_change_pct=3.0) == []


def test_rank_universe_without_threshold_keeps_everything():
    okx = _FakeOKX(changes={"AAA-USDT": 0.005, "BBB-USDT": -0.01})
    ranked = rank_universe(okx, universe=["AAA-USDT", "BBB-USDT"])
    assert [s.inst_id for s in ranked] == ["AAA-USDT", "BBB-USDT"]


def test_rank_universe_skips_symbols_that_error():
    okx = _FakeOKX(changes={"GOOD-USDT": 0.10})
    ranked = rank_universe(okx, universe=["GOOD-USDT", "MISSING-USDT"], min_change_pct=3.0)
    assert [s.inst_id for s in ranked] == ["GOOD-USDT"]
