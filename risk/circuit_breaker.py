"""Halts trading on drawdown or a losing streak. Persisted to disk so a
process restart cannot silently clear a halt.
"""
import json
from datetime import date
from pathlib import Path


class TradingHalted(Exception):
    pass


class CircuitBreaker:
    def __init__(self, pool: str, state_dir: Path, daily_loss_halt_pct: float, max_consecutive_losses: int):
        self.pool = pool
        self.daily_loss_halt_pct = daily_loss_halt_pct
        self.max_consecutive_losses = max_consecutive_losses
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = self.state_dir / f"breaker_{pool}.json"

    def _default_state(self, pool_value: float) -> dict:
        return {
            "date": str(date.today()),
            "day_start_value": pool_value,
            "consecutive_losses": 0,
            "halted": False,
            "halt_reason": None,
        }

    def _load(self, pool_value: float) -> dict:
        if not self._state_file.exists():
            return self._default_state(pool_value)
        data = json.loads(self._state_file.read_text())
        if data.get("date") != str(date.today()):
            # New day: reset drawdown tracking, but an unresolved halt stays
            # active until explicitly reset — a new day doesn't excuse it.
            fresh = self._default_state(pool_value)
            fresh["halted"] = data.get("halted", False)
            fresh["halt_reason"] = data.get("halt_reason")
            return fresh
        return data

    def _save(self, data: dict) -> None:
        self._state_file.write_text(json.dumps(data))

    def check(self, pool_value: float) -> None:
        """Raise TradingHalted if trading should not proceed right now."""
        state = self._load(pool_value)

        if state["halted"]:
            self._save(state)
            raise TradingHalted(f"[{self.pool}] Halted: {state['halt_reason']}")

        start = state["day_start_value"]
        if start > 0:
            drawdown_pct = (pool_value - start) / start
            if drawdown_pct < -self.daily_loss_halt_pct:
                state["halted"] = True
                state["halt_reason"] = f"Daily drawdown {drawdown_pct:.1%} breached -{self.daily_loss_halt_pct:.0%} limit"
                self._save(state)
                raise TradingHalted(f"[{self.pool}] {state['halt_reason']}")

        self._save(state)

    def record_trade_result(self, won: bool, loss_pct_of_pool: float | None = None) -> None:
        """Call after a trade closes with a known win/loss outcome.

        `loss_pct_of_pool` es cuanto costo la perdida como fraccion del
        pool. Solo las perdidas materiales cuentan para la racha.

        Por que: el contador trataba igual una perdida de $200.000 que una
        de $300. Eso tenia sentido cuando toda posicion era el 20% del pool,
        pero con sizing por conviccion (execution/sizing.py) una apuesta
        especulativa es del 0,5%, y una cartera de apuestas asimetricas
        tiene rachas largas de perdidas chicas por diseno matematico: con
        `max_consecutive_losses=3` se auto-detendria el primer dia, de forma
        permanente, sin haber perdido casi nada.

        Esto NO afloja la proteccion real. El corte por drawdown diario
        (-10% del pool) sigue exactamente igual y es el que responde ante
        una perdida grande, venga de una operacion o de veinte. Lo que se
        corrige es que el contador de rachas ahora mide dano en vez de
        contar eventos. Sin el argumento se comporta como antes.
        """
        # Debajo de esto una perdida es ruido de operacion, no evidencia de
        # que la estrategia este rota, que es lo que la racha intenta detectar.
        material_loss_pct = 0.02

        state = self._load(pool_value=0)  # value unused for this update
        if won:
            state["consecutive_losses"] = 0
        elif loss_pct_of_pool is not None and loss_pct_of_pool < material_loss_pct:
            pass  # perdida inmaterial: no rompe la racha ni la incrementa
        else:
            state["consecutive_losses"] += 1
            if state["consecutive_losses"] >= self.max_consecutive_losses:
                state["halted"] = True
                state["halt_reason"] = f"{state['consecutive_losses']} consecutive losing trades"
        self._save(state)

    def reset(self) -> None:
        """Manual reset — a human decided it's OK to resume trading."""
        if self._state_file.exists():
            self._state_file.unlink()
