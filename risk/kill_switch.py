"""Corte de emergencia que el propio bot consulta antes de cada orden.

api/bot_control.py ya podia deshabilitar las tareas del Programador, pero
eso tiene tres huecos que importan justo en una emergencia:

  1. No detiene un ciclo que YA esta corriendo: ese sigue colocando
     ordenes hasta terminar.
  2. No cubre una corrida manual (`python execution/orchestrator.py ...`),
     que no pasa por el Programador en absoluto -- durante la auditoria
     del 2026-09-10 se corrieron varias asi.
  3. No tiene granularidad: es todo o nada, no "parar solo crypto".

Este switch vive en un archivo y lo consulta el orchestrator antes de
colocar cada orden, asi que corta pase lo que pase y por donde sea que se
haya lanzado el ciclo, y corta en la siguiente orden aunque el ciclo ya
este a mitad de camino.

Lo que NO corta, a proposito: las SALIDAS. Igual que el circuit breaker
(ver el P0 de execution/orchestrator.py), un corte tiene que frenar riesgo
NUEVO y jamas frenar la reduccion del riesgo ya tomado. Un kill switch que
impidiera ejecutar un stop-loss dejaria las posiciones abiertas sin
proteccion, que es lo contrario de lo que se busca al accionarlo.
"""
import json
from pathlib import Path

from config.state_store import write_json_atomic

_FILENAME = "kill_switch.json"


def _path(state_dir: Path) -> Path:
    return Path(state_dir) / _FILENAME


def engage(state_dir: Path, reason: str, pools: list[str] | None = None) -> dict:
    """Corta la apertura de posiciones nuevas. `pools=None` corta todo."""
    payload = {"active": True, "reason": reason, "pools": pools}
    write_json_atomic(_path(state_dir), payload)
    return payload


def release(state_dir: Path) -> None:
    """Reanuda. Explicito y manual: un corte no se levanta solo."""
    _path(state_dir).unlink(missing_ok=True)


def blocked_reason(state_dir: Path, pool: str) -> str | None:
    """Motivo por el que este pool no puede abrir posiciones, o None.

    Ante un archivo ilegible se corta igual: si no se puede determinar el
    estado del switch, la respuesta segura es no operar (seccion 35), no
    asumir que esta todo bien.
    """
    path = _path(state_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "kill switch ilegible -- se corta por precaucion"

    if not data.get("active"):
        return None
    pools = data.get("pools")
    if pools and pool not in pools:
        return None
    return data.get("reason") or "kill switch activo"
