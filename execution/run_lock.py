"""Un solo ciclo por pool a la vez.

Encontrado en la auditoria del 2026-09-10: no existia ninguna proteccion a
nivel aplicacion contra dos ciclos simultaneos del mismo pool. La unica
barrera era MultipleInstancesPolicy=IgnoreNew del Programador de tareas de
Windows, que es invisible desde el codigo y NO cubre:

  - una corrida manual mientras la programada esta en curso (paso de
    verdad ese mismo dia: scan manual a las 14:59:33 y scan programado a
    las 15:00:29, con el ciclo manual todavia ejecutando ordenes a las
    15:00:04);
  - dos maquinas distintas apuntando a la misma cuenta;
  - cualquier invocacion futura fuera del Programador.

Con dos ciclos en paralelo, ambos leen el mismo positions_<pool>.json,
calculan el mismo `held`, ven los mismos candidatos y compran los mismos
simbolos. Ademas el ultimo en escribir el archivo de estado pisa al otro:
una de las posiciones recien abiertas queda sin registrar, y una posicion
sin registrar es una posicion sin stop-loss.

Se usa un archivo creado con O_EXCL, que es atomico tanto en Windows como
en POSIX -- sin dependencias nuevas ni APIs especificas de plataforma.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path


class AlreadyRunning(Exception):
    pass


# El Programador de tareas corta cada ciclo a los 10 minutos
# (ExecutionTimeLimit=PT10M). Pasado ese margen mas holgura, un lock que
# sigue ahi es de un proceso muerto (crash, corte de luz, kill -9), no de
# uno vivo: quedarse bloqueado para siempre por eso seria peor que el
# problema que este lock resuelve.
DEFAULT_STALE_AFTER_SECONDS = 15 * 60


class RunLock:
    def __init__(self, name: str, state_dir: Path, stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS):
        self.name = name
        self.state_dir = Path(state_dir)
        self.stale_after_seconds = stale_after_seconds
        self._path = self.state_dir / f"run_{name}.lock"
        self._acquired = False
        self.took_over_stale_lock = False

    def _payload(self) -> str:
        return json.dumps({
            "pid": os.getpid(),
            "started_at": datetime.now(timezone.utc).isoformat(),
        })

    def _age_seconds(self) -> float | None:
        """Antiguedad del lock existente, o None si no se puede determinar
        (archivo ilegible o corrupto -- se trata como viejo para no quedar
        bloqueado por un archivo roto)."""
        try:
            data = json.loads(self._path.read_text())
            started = datetime.fromisoformat(data["started_at"])
        except (OSError, ValueError, KeyError):
            return None
        return (datetime.now(timezone.utc) - started).total_seconds()

    def acquire(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            age = self._age_seconds()
            if age is not None and age < self.stale_after_seconds:
                raise AlreadyRunning(
                    f"[{self.name}] ya hay un ciclo en curso (lock de hace {age:.0f}s). "
                    f"No se arranca un segundo: dos ciclos a la vez compran lo mismo dos veces."
                ) from None
            # Lock viejo o ilegible: el proceso que lo dejo ya no existe.
            self._path.unlink(missing_ok=True)
            self.took_over_stale_lock = True
            fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)

        with os.fdopen(fd, "w") as f:
            f.write(self._payload())
        self._acquired = True

    def release(self) -> None:
        if self._acquired:
            self._path.unlink(missing_ok=True)
            self._acquired = False

    def __enter__(self) -> "RunLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()
