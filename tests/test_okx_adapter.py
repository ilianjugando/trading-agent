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


def test_equity_is_computed_from_market_prices_not_the_exchange_field():
    """Medido en vivo (2026-09-10): OKX reportaba eqUsd de STX en $5.575
    cuando su valor real a precio de mercado era $1.091 -- 5,1x de error en
    un campo del propio exchange. pool_value depende de esto, y con el
    todo el sizing y el baseline de drawdown, asi que la cifra que manda
    tiene que ser la calculada con la MISMA fuente de precio que usan los
    stops y el P&L."""
    adapter = _adapter()
    adapter.account.get_account_balance = lambda **kw: {
        "data": [{
            "totalEq": "5675.49",   # inflado por el exchange
            "details": [
                {"ccy": "USDT", "availBal": "100.0", "eqUsd": "100.0"},
                {"ccy": "STX", "availBal": "4128.175692", "eqUsd": "5575.49"},
            ],
        }],
    }
    adapter.market.get_tickers = lambda **kw: {
        "data": [{"instId": "STX-USDT", "last": "0.2645"}],
    }

    equity = adapter.get_equity()

    assert equity["equity_usd"] == 1191.9   # 100 efectivo + 4128.175692 * 0.2645
    assert equity["exchange_reported_usd"] == 5675.49
    assert equity["divergence_pct"] > 100   # la divergencia queda visible


def test_holdings_without_a_price_are_reported_not_silently_zeroed():
    """Seccion 15: un cero silencioso es indistinguible de 'no hay nada'."""
    adapter = _adapter()
    adapter.account.get_account_balance = lambda **kw: {
        "data": [{
            "totalEq": "100.0",
            "details": [
                {"ccy": "USDT", "availBal": "100.0"},
                {"ccy": "RAROCOIN", "availBal": "5.0"},
            ],
        }],
    }
    adapter.market.get_tickers = lambda **kw: {"data": []}

    equity = adapter.get_equity()

    assert equity["equity_usd"] == 100.0
    assert equity["unpriced"] == {"RAROCOIN": 5.0}


def test_get_filled_base_qty_returns_just_the_quantity(monkeypatch):
    adapter = _adapter()
    adapter.trade.get_order = lambda **kwargs: {"data": [{"accFillSz": "42.5", "avgPx": "1.0"}]}
    monkeypatch.setattr("brokers.okx_adapter.time.sleep", lambda *_: None)

    assert adapter.get_filled_base_qty("BTC-USDT", "123") == 42.5


def test_order_carries_an_idempotency_key(monkeypatch):
    """Seccion 12: toda orden lleva clOrdId propio, para poder preguntarle
    despues a OKX si esa orden concreta existe."""
    adapter = _adapter()
    sent = {}

    def _capture(**kwargs):
        sent.update(kwargs)
        return {"data": [{"sCode": "0", "sMsg": "", "ordId": "1"}]}

    adapter.trade.place_order = _capture
    adapter.trade.get_order = lambda **kw: {"data": [{"accFillSz": "1", "avgPx": "1"}]}
    monkeypatch.setattr("brokers.okx_adapter.time.sleep", lambda *_: None)

    result = adapter.place_market_order("BTC-USDT", 100.0, "buy")

    assert sent["clOrdId"].startswith("ta")
    assert len(sent["clOrdId"]) <= 32, "OKX limita clOrdId a 32 caracteres"
    assert result["clOrdId"] == sent["clOrdId"]


def test_lost_response_does_not_lose_an_order_that_actually_landed(monkeypatch):
    """El caso peligroso: la peticion sale, la respuesta se pierde. Sin
    desambiguar, el sistema lo registra como fallo y sigue -- pero la orden
    existe, y una posicion real sin registrar es una posicion sin
    stop-loss. Se consulta por clOrdId antes de darla por perdida."""
    adapter = _adapter()

    def _network_dies(**kwargs):
        raise ConnectionError("respuesta perdida")

    adapter.trade.place_order = _network_dies
    # OKX SI tiene la orden: llego a ejecutarse.
    adapter.trade.get_order = lambda **kw: {
        "data": [{"ordId": "555", "accFillSz": "2.5", "avgPx": "40000"}],
    }
    monkeypatch.setattr("brokers.okx_adapter.time.sleep", lambda *_: None)

    result = adapter.place_market_order("BTC-USDT", 100.0, "buy")

    assert result["ordId"] == "555", "la orden real no se puede perder"
    assert result["qty"] == 2.5


def test_a_genuinely_failed_order_still_raises(monkeypatch):
    """Lo contrario: si la orden de verdad no existe, el fallo se propaga.
    No se puede inventar una posicion que no ocurrio."""
    adapter = _adapter()

    def _network_dies(**kwargs):
        raise ConnectionError("respuesta perdida")

    adapter.trade.place_order = _network_dies
    adapter.trade.get_order = lambda **kw: {"data": []}  # OKX no la tiene
    monkeypatch.setattr("brokers.okx_adapter.time.sleep", lambda *_: None)

    try:
        adapter.place_market_order("BTC-USDT", 100.0, "buy")
        assert False, "se esperaba que el fallo se propagara"
    except ConnectionError:
        pass
