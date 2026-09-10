"""Tracks open positions and their current stop-loss level, persisted to
disk so a trailing stop survives process restarts (each run is one-shot).
"""
import json
from pathlib import Path

from config.state_store import write_json_atomic


class PositionTracker:
    def __init__(self, pool: str, state_dir: Path):
        self.pool = pool
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._file = self.state_dir / f"positions_{pool}.json"

    def _load(self) -> dict:
        if not self._file.exists():
            return {}
        return json.loads(self._file.read_text())

    def _save(self, data: dict) -> None:
        # Atomico: un corte a mitad de escritura no puede dejar el registro
        # de posiciones corrupto (ver config/state_store.py).
        write_json_atomic(self._file, data)

    def get(self, symbol: str) -> dict | None:
        return self._load().get(symbol)

    def all_open(self) -> dict:
        return self._load()

    def open(self, symbol: str, entry_price: float, qty: float, stop: float) -> None:
        data = self._load()
        data[symbol] = {"entry_price": entry_price, "qty": qty, "stop": stop}
        self._save(data)

    def update_stop(self, symbol: str, new_stop: float) -> None:
        """Stops only ever move up -- that's the trailing-stop rule."""
        data = self._load()
        if symbol in data and new_stop > data[symbol]["stop"]:
            data[symbol]["stop"] = new_stop
            self._save(data)

    def close(self, symbol: str) -> dict | None:
        data = self._load()
        pos = data.pop(symbol, None)
        self._save(data)
        return pos
