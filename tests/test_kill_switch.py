from risk import kill_switch


def test_no_switch_means_no_block(tmp_path):
    assert kill_switch.blocked_reason(tmp_path, "crypto") is None


def test_engaged_switch_blocks_every_pool(tmp_path):
    kill_switch.engage(tmp_path, reason="algo raro en el mercado")
    assert "algo raro" in kill_switch.blocked_reason(tmp_path, "crypto")
    assert "algo raro" in kill_switch.blocked_reason(tmp_path, "stocks")


def test_can_stop_just_one_pool(tmp_path):
    """Granularidad (seccion 36): parar solo una clase de activo."""
    kill_switch.engage(tmp_path, reason="OKX inestable", pools=["crypto"])
    assert kill_switch.blocked_reason(tmp_path, "crypto") is not None
    assert kill_switch.blocked_reason(tmp_path, "stocks") is None


def test_release_resumes(tmp_path):
    kill_switch.engage(tmp_path, reason="parar todo")
    kill_switch.release(tmp_path)
    assert kill_switch.blocked_reason(tmp_path, "crypto") is None


def test_unreadable_switch_blocks_rather_than_assuming_all_is_well(tmp_path):
    """Seccion 35: ante incertidumbre critica, no operar. Si no se puede
    leer el estado del corte, la respuesta segura es cortar."""
    (tmp_path / "kill_switch.json").write_text("esto no es json")
    assert kill_switch.blocked_reason(tmp_path, "crypto") is not None
