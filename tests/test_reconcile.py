from execution.reconcile import compare


def test_clean_when_everything_matches():
    r = compare("stocks", {"AMBA": {"qty": 88}}, {"AMBA": 88})
    assert r.clean


def test_detects_a_phantom_position():
    """Caso real (2026-09-10, OKX): 4 posiciones registradas cuya orden de
    compra nunca se lleno. Su stop-loss no protegia nada y al dispararse la
    venta se rechazaba."""
    r = compare("crypto", {"IOST-USDT": {"qty": 801617.0}}, {})
    assert r.phantom == {"IOST-USDT": 801617.0}
    assert not r.clean


def test_detects_an_untracked_holding():
    """Peor que una fantasma: una posicion real que el bot no sabe que
    tiene no tiene stop-loss en absoluto."""
    r = compare("crypto", {}, {"BTC": 0.5})
    assert r.untracked == {"BTC": 0.5}
    assert not r.clean


def test_detects_a_quantity_mismatch():
    """Caso real: llenado parcial contabilizado como completo (ARB, 55%)."""
    r = compare("crypto", {"ARB-USDT": {"qty": 9479.0}}, {"ARB-USDT": 4248.0})
    assert "ARB-USDT" in r.mismatched
    assert r.mismatched["ARB-USDT"]["real"] == 4248.0


def test_dust_from_fees_is_within_tolerance():
    """Las comisiones dejan siempre un resto. Reportarlo cada ciclo
    entrenaria a ignorar la alerta, que es como una alerta deja de servir."""
    r = compare("crypto", {"KMNO-USDT": {"qty": 39583.97}}, {"KMNO-USDT": 39576.08})
    assert r.clean


def test_report_is_json_serializable():
    import json

    r = compare("crypto", {"X-USDT": {"qty": 10}}, {})
    json.dumps(r.as_log_record())
