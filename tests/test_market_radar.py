import pandas as pd

from signals import market_radar


class _FakeQuery:
    """Doble minimo de tradingview_screener.Query -- solo lo que
    market_radar.py realmente encadena (.where, .order_by,
    .set_property, .get_scanner_data)."""

    def __init__(self, df: pd.DataFrame):
        self._df = df

    def where(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def set_property(self, *args, **kwargs):
        return self

    def get_scanner_data(self):
        return len(self._df), self._df


def _cex_df(rows):
    return pd.DataFrame(rows, columns=["ticker", "exchange.tr", "close", "24h_close_change|5", "24h_vol|5"])


def _dex_df(rows):
    return pd.DataFrame(
        rows,
        columns=["ticker", "exchange.tr", "blockchain-id.tr", "close", "24h_close_change|5", "dex_trading_volume_24h"],
    )


def test_cex_movers_maps_real_columns_correctly(monkeypatch):
    df = _cex_df([["BINANCE:DOGEUSDT", "Binance", 0.15, 42.3, 5_000_000.0]])
    monkeypatch.setattr(market_radar, "crypto", lambda: _FakeQuery(df))

    result = market_radar.cex_movers()
    assert len(result) == 1
    m = result[0]
    assert m.symbol == "BINANCE:DOGEUSDT"
    assert m.exchange == "Binance"
    assert m.change_24h_pct == 42.3
    assert m.volume_24h_usd == 5_000_000.0
    assert m.suspicious is False


def test_dex_movers_includes_blockchain_field(monkeypatch):
    df = _dex_df([["RAYDIUM:FOOBAR", "Raydium", "Solana", 0.002, 88.0, 60_000.0]])
    monkeypatch.setattr(market_radar, "crypto_dex", lambda: _FakeQuery(df))

    result = market_radar.dex_movers()
    assert len(result) == 1
    assert result[0].blockchain == "Solana"


def test_extreme_change_pct_is_flagged_not_hidden(monkeypatch):
    """Un par de DEX recien creado puede mostrar un cambio % sin sentido
    porque el precio de referencia previo era casi cero -- se marca, no se
    esconde ni se inventa un numero distinto."""
    df = _dex_df([["UNISWAP:NEWCOIN", "Uniswap", "Base", 5.0, 2_000_000.0, 100_000.0]])
    monkeypatch.setattr(market_radar, "crypto_dex", lambda: _FakeQuery(df))

    result = market_radar.dex_movers()
    assert result[0].change_24h_pct == 2_000_000.0  # el numero real, sin tocar
    assert result[0].suspicious is True


def test_empty_result_is_a_valid_outcome(monkeypatch):
    monkeypatch.setattr(market_radar, "crypto", lambda: _FakeQuery(_cex_df([])))
    assert market_radar.cex_movers() == []
