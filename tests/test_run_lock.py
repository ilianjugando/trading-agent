import json
from datetime import datetime, timedelta, timezone

import pytest

from execution.run_lock import AlreadyRunning, RunLock


def test_second_run_of_the_same_pool_is_refused(tmp_path):
    """El escenario concreto de la seccion 19: dos ciclos leen el mismo
    estado, ven los mismos candidatos y compran lo mismo dos veces."""
    with RunLock("crypto", tmp_path):
        with pytest.raises(AlreadyRunning):
            RunLock("crypto", tmp_path).acquire()


def test_lock_is_released_on_exit(tmp_path):
    with RunLock("crypto", tmp_path):
        pass
    with RunLock("crypto", tmp_path):  # no debe levantar
        pass


def test_lock_is_released_even_if_the_cycle_crashes(tmp_path):
    """Un crash a mitad de ciclo no puede dejar el pool bloqueado."""
    with pytest.raises(RuntimeError):
        with RunLock("crypto", tmp_path):
            raise RuntimeError("el ciclo exploto")

    with RunLock("crypto", tmp_path):  # el lock quedo liberado
        pass


def test_different_pools_do_not_block_each_other(tmp_path):
    with RunLock("crypto", tmp_path):
        with RunLock("stocks", tmp_path):  # pools independientes
            pass


def test_stale_lock_from_a_dead_process_is_taken_over(tmp_path):
    """Un kill -9 o un corte de luz deja el archivo. Quedarse bloqueado
    para siempre por eso seria peor que el problema que el lock resuelve
    -- el ciclo del Programador se corta a los 10 minutos, asi que un lock
    mas viejo que eso es de un proceso que ya no existe."""
    lock_file = tmp_path / "run_crypto.lock"
    lock_file.write_text(json.dumps({
        "pid": 999999,
        "started_at": (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(),
    }))

    lock = RunLock("crypto", tmp_path)
    lock.acquire()
    assert lock.took_over_stale_lock
    lock.release()


def test_fresh_lock_is_never_taken_over(tmp_path):
    """Lo contrario: un lock reciente es de un ciclo VIVO y se respeta."""
    lock_file = tmp_path / "run_crypto.lock"
    lock_file.write_text(json.dumps({
        "pid": 999999,
        "started_at": (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat(),
    }))

    with pytest.raises(AlreadyRunning):
        RunLock("crypto", tmp_path).acquire()


def test_corrupt_lock_file_does_not_block_forever(tmp_path):
    """Un archivo de lock ilegible no puede dejar el pool trabado."""
    (tmp_path / "run_crypto.lock").write_text("esto no es json")

    lock = RunLock("crypto", tmp_path)
    lock.acquire()
    assert lock.took_over_stale_lock
    lock.release()
