"""Hard spend limits, enforced independently of any model output.

State is persisted to disk (state/spend_<pool>.json) so limits survive
process restarts within the same day — an orchestrator crash-and-retry
loop can't be used to bypass the daily cap.

La frontera del dia es UTC, igual que risk/circuit_breaker.py y que todos
los timestamps que escribe el orchestrator. Antes era date.today() (fecha
LOCAL de la maquina): el "dia" del limite de gasto no coincidia con el
"dia" de los logs, y mover la maquina de zona horaria corria la frontera
-- lo que en el peor caso reinicia el presupuesto diario antes de tiempo.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from config.state_store import write_json_atomic


class SpendLimitError(Exception):
    pass


def _utc_today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class SpendGuard:
    def __init__(self, pool: str, state_dir: Path, max_trade_pct: float, daily_loss_halt_pct: float):
        self.pool = pool
        self.max_trade_pct = max_trade_pct
        self.daily_loss_halt_pct = daily_loss_halt_pct
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = self.state_dir / f"spend_{pool}.json"

    def _load(self) -> dict:
        if not self._state_file.exists():
            return {"date": _utc_today(), "spent": 0.0}
        data = json.loads(self._state_file.read_text())
        if data.get("date") != _utc_today():
            return {"date": _utc_today(), "spent": 0.0}
        return data

    def _save(self, data: dict) -> None:
        write_json_atomic(self._state_file, data)

    def check_and_record(self, usd_amount: float, pool_value: float) -> None:
        if usd_amount <= 0:
            raise SpendLimitError(f"Invalid trade amount: {usd_amount}")

        max_single = pool_value * self.max_trade_pct
        if usd_amount > max_single:
            raise SpendLimitError(
                f"[{self.pool}] Trade ${usd_amount:.2f} exceeds per-trade cap "
                f"${max_single:.2f} ({self.max_trade_pct:.0%} of pool)"
            )

        state = self._load()
        daily_cap = pool_value * self.daily_loss_halt_pct * 5  # generous cap on *turnover*, not loss
        if state["spent"] + usd_amount > daily_cap:
            raise SpendLimitError(
                f"[{self.pool}] Daily turnover ${state['spent']:.2f} + ${usd_amount:.2f} "
                f"would exceed cap ${daily_cap:.2f}"
            )

        state["spent"] += usd_amount
        self._save(state)

    def refund(self, usd_amount: float) -> None:
        """Devuelve al presupuesto diario lo que reservo una orden que
        despues no se ejecuto.

        check_and_record() suma ANTES de mandar la orden y tiene que seguir
        siendo asi: reservar despues de ejecutar deja una ventana donde el
        tope no existe. Pero sin esta devolucion, una orden que el broker
        rechaza quemaba rotacion diaria que nunca se uso -- y unos cuantos
        rechazos seguidos (un simbolo que el entorno demo no lista, un
        ticker delistado) dejaban al bot sin presupuesto por el resto del
        dia sin haber operado nada.
        """
        if usd_amount <= 0:
            return
        state = self._load()
        # max(0) y no una resta pelada: si el archivo se reinicio por cambio
        # de fecha entre la reserva y la devolucion, restar dejaria el gasto
        # del dia en negativo, o sea presupuesto regalado.
        state["spent"] = max(0.0, state["spent"] - usd_amount)
        self._save(state)
