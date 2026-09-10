"""Escritura atomica de los archivos de estado.

Encontrado en la auditoria del 2026-09-10: posiciones, circuit breaker y
spend guard se guardaban con `path.write_text(json.dumps(data))`, que
primero trunca el archivo y despues escribe. Si el proceso muere en ese
intervalo, el archivo queda vacio o cortado a la mitad.

No es hipotetico: el Programador de tareas de Windows corta cada ciclo a
los 10 minutos (ExecutionTimeLimit=PT10M) matando el proceso, y un ciclo
de crypto que espera respuestas de OKX y de tres modelos LLM puede
acercarse a ese limite. Si el corte cae sobre la escritura de
positions_<pool>.json, el bot pierde el registro de TODAS las posiciones
abiertas de golpe -- y una posicion que el bot no registra es una posicion
sin stop-loss. Lo mismo con el estado del circuit breaker: un halt activo
podria desaparecer.

`os.replace` es atomico tanto en Windows como en POSIX: o esta el archivo
viejo entero, o el nuevo entero, nunca una mezcla.

Vive en config/ porque lo necesitan execution/ y risk/ por igual, y
ninguno de los dos deberia depender del otro.
"""
import json
import os
import tempfile
from pathlib import Path


def write_json_atomic(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Serializar ANTES de tocar el archivo destino: si los datos no son
    # serializables, el archivo bueno que ya estaba sigue intacto.
    payload = json.dumps(data)

    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())  # el dato en disco, no solo en el buffer del SO
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
