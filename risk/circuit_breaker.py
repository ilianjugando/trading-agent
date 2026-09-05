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

    def record_trade_result(self, won: bool) -> None:
        """Call after a trade closes with a known win/loss outcome."""
        state = self._load(pool_value=0)  # value unused for this update
        if won:
            state["consecutive_losses"] = 0
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
