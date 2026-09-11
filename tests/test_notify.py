from execution import notify


def test_only_material_events_notify():
    """Un aviso por cada ciclo entrena a ignorarlos. Escaneos, 'ya la
    tenemos' y stops subidos se miran en el dashboard cuando uno quiere."""
    for ruido in ("scan", "tournament", "skipped_already_held", "stop_trailed",
                  "market_closed", "skipped_already_running"):
        assert notify.format_event({"result": ruido, "pool": "crypto"}) is None


def test_material_events_do_notify():
    for importante in ("executed", "stopped_out", "halted", "exit_error",
                       "order_rejected", "position_reconciliation_alert"):
        assert notify.format_event({"result": importante, "pool": "crypto"}) is not None


def test_a_buy_reads_as_a_sentence():
    text = notify.format_event({
        "pool": "crypto", "result": "executed", "symbol": "ETH-USDT",
        "sizing": {"usd": 1234.5},
    })
    assert "ETH-USDT" in text
    assert "1,234.50" in text
    assert "Compra ejecutada" in text


def test_a_stop_out_says_whether_it_won():
    text = notify.format_event({
        "pool": "stocks", "result": "stopped_out", "symbol": "AMBA",
        "won": False, "price": 68.1,
    })
    assert "PERDIDA" in text and "AMBA" in text


def test_send_never_raises_even_with_a_broken_transport(monkeypatch):
    """Un fallo de red avisando no puede tumbar un ciclo de trading."""
    def _boom(*a, **kw):
        raise ConnectionError("sin internet")

    monkeypatch.setattr(notify, "_telegram", _boom)
    monkeypatch.setattr(notify, "_email", _boom)
    assert notify.send({"result": "executed", "pool": "crypto", "symbol": "X"}) is False


def test_unconfigured_is_not_an_error(monkeypatch):
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "NOTIFY_EMAIL_TO", "SMTP_HOST"):
        monkeypatch.delenv(var, raising=False)
    assert notify.is_configured() is False
    assert notify.send({"result": "executed", "pool": "crypto"}) is False
