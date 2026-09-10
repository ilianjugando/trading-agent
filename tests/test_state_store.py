import json

import pytest

from config.state_store import write_json_atomic


def test_writes_and_reads_back(tmp_path):
    target = tmp_path / "estado.json"
    write_json_atomic(target, {"a": 1})
    assert json.loads(target.read_text()) == {"a": 1}


def test_a_failed_write_leaves_the_previous_file_intact(tmp_path):
    """El punto de todo esto: si algo falla a mitad de camino, el archivo
    bueno que ya estaba tiene que seguir entero. Con write_text() el
    archivo primero se trunca, asi que un fallo ahi lo deja vacio -- y un
    positions_<pool>.json vacio significa que el bot perdio el registro de
    todas sus posiciones abiertas, o sea todas sin stop-loss."""
    target = tmp_path / "positions_crypto.json"
    write_json_atomic(target, {"ARB-USDT": {"qty": 100, "stop": 0.1}})

    class _NoSerializable:
        pass

    with pytest.raises(TypeError):
        write_json_atomic(target, {"roto": _NoSerializable()})

    assert json.loads(target.read_text()) == {"ARB-USDT": {"qty": 100, "stop": 0.1}}


def test_no_temp_files_are_left_behind(tmp_path):
    target = tmp_path / "estado.json"
    write_json_atomic(target, {"a": 1})

    class _NoSerializable:
        pass

    with pytest.raises(TypeError):
        write_json_atomic(target, {"roto": _NoSerializable()})

    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "estado.json"]
    assert leftovers == []


def test_overwrites_cleanly(tmp_path):
    target = tmp_path / "estado.json"
    write_json_atomic(target, {"version": 1})
    write_json_atomic(target, {"version": 2})
    assert json.loads(target.read_text()) == {"version": 2}


def test_creates_missing_parent_directory(tmp_path):
    target = tmp_path / "nueva" / "carpeta" / "estado.json"
    write_json_atomic(target, {"a": 1})
    assert target.exists()
