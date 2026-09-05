"""Hard spend limits, enforced independently of any model output.

State is persisted to disk (state/spend_<pool>.json) so limits survive
process restarts within the same day — an orchestrator crash-and-retry
loop can't be used to bypass the daily cap.
"""
import json
from datetime import date
from pathlib import Path


class SpendLimitError(Exception):
    pass


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
            return {"date": str(date.today()), "spent": 0.0}
        data = json.loads(self._state_file.read_text())
        if data.get("date") != str(date.today()):
            return {"date": str(date.today()), "spent": 0.0}
        return data

    def _save(self, data: dict) -> None:
        self._state_file.write_text(json.dumps(data))

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
