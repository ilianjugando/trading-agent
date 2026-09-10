from brokers.okx_adapter import OKXAdapter


def _adapter() -> OKXAdapter:
    return OKXAdapter("key", "secret", "phrase", "1")


def test_buy_returns_actual_fill_qty_and_price_not_an_estimate(monkeypatch):
    """Bug found live (2026-09-10): the caller used to estimate qty as
    usd_amount/current_price, silently ignoring OKX's fee taken out of the
    received base currency. That estimate is consistently too high, and a
    later sell for the (nonexistent) extra amount gets rejected outright
    -- exactly when a stop-loss needs to work. place_market_order must
    return the REAL fill, queried back from OKX, not a guess."""
    adapter = _adapter()

    monkeypatch.setattr(adapter.trade, "place_order", lambda **kwargs: {
        "data": [{"sCode": "0", "sMsg": "", "ordId": "999"}],
    })
    # Real fill is less than a naive usd/price estimate would give, because
    # OKX's fee comes out of the base currency received.
    monkeypatch.setattr(adapter.trade, "get_order", lambda **kwargs: {
        "data": [{"accFillSz": "126415081.377", "avgPx": "0.0000019466811"}],
    })
    monkeypatch.setattr("brokers.okx_adapter.time.sleep", lambda *_: None)

    result = adapter.place_market_order("CAT-USDT", 250.0, "buy")
    assert result["qty"] == 126415081.377
    assert result["price"] == 0.0000019466811


def test_sell_does_not_query_fill_details():
    """A sell result feeds straight into trades.log, not positions.open()
    -- no need to pay the extra API round trip a buy needs."""
    adapter = _adapter()
    calls = []
    adapter.trade.place_order = lambda **kwargs: {"data": [{"sCode": "0", "sMsg": "", "ordId": "1"}]}
    adapter.trade.get_order = lambda **kwargs: calls.append(1) or {"data": [{"accFillSz": "0", "avgPx": "0"}]}

    result = adapter.place_market_order("CAT-USDT", 100.0, "sell")
    assert "qty" not in result
    assert "price" not in result
    assert calls == []


def test_rejected_order_raises_before_any_fill_lookup():
    adapter = _adapter()
    adapter.trade.place_order = lambda **kwargs: {
        "data": [{"sCode": "51008", "sMsg": "insufficient balance", "ordId": ""}],
    }
    try:
        adapter.place_market_order("CAT-USDT", 250.0, "buy")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "51008" in str(e)


def test_get_filled_base_qty_returns_just_the_quantity(monkeypatch):
    adapter = _adapter()
    adapter.trade.get_order = lambda **kwargs: {"data": [{"accFillSz": "42.5", "avgPx": "1.0"}]}
    monkeypatch.setattr("brokers.okx_adapter.time.sleep", lambda *_: None)

    assert adapter.get_filled_base_qty("BTC-USDT", "123") == 42.5
