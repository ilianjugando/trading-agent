"""Estrategias definidas como datos, no como codigo.

Una estrategia custom es una lista de condiciones sobre los indicadores
que ya existen en signals/indicators.py. Se guarda en JSON, se puede
backtestear y se puede operar en vivo -- sin escribir Python ni
redeployar nada.

Por que una lista de condiciones y no un lenguaje de expresiones: un
parser de expresiones sobre datos que el usuario escribe es una
superficie de ataque (y de bugs) enorme a cambio de una flexibilidad que
ninguna de las estrategias reales de este proyecto necesita. Las cuatro
que ya operan (momentum, mean_reversion, trend_follow, breakout) se
expresan enteras con esto.

La SALIDA no se define aca a proposito: sale de asymmetry.compute(),
igual que en el bot en vivo y en el backtest. Una estrategia con su
propia regla de salida inventada produce una curva linda que despues no
aparece operando.
"""
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from config.state_store import write_json_atomic
from signals.indicators import rsi, sma_trend
from signals.strategies import Proposal

# Cada indicador reducido a un solo numero (o string) comparable, sobre la
# ventana de cierres disponible hasta esa barra.
INDICATORS = {
    "rsi": lambda c: rsi(c),
    "sma_trend": lambda c: sma_trend(c),
    "change_pct": lambda c: (c[-1] - c[-2]) / c[-2] * 100 if len(c) >= 2 and c[-2] else None,
    "change_5_pct": lambda c: (c[-1] - c[-6]) / c[-6] * 100 if len(c) >= 6 and c[-6] else None,
    "above_high_20": lambda c: c[-1] > max(c[-21:-1]) if len(c) >= 21 else None,
    "below_low_20": lambda c: c[-1] < min(c[-21:-1]) if len(c) >= 21 else None,
}

OPS = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


@dataclass
class Rule:
    indicator: str
    op: str
    value: float | str | bool

    def holds(self, closes: list[float]) -> bool:
        fn = INDICATORS.get(self.indicator)
        op = OPS.get(self.op)
        if fn is None or op is None:
            return False
        actual = fn(closes)
        if actual is None:
            return False  # sin dato no se opera: el lado seguro
        try:
            return bool(op(actual, self.value))
        except TypeError:
            return False  # comparacion entre tipos incompatibles


@dataclass
class StrategySpec:
    name: str
    entry: list[Rule] = field(default_factory=list)
    description: str = ""

    def __post_init__(self):
        self.entry = [r if isinstance(r, Rule) else Rule(**r) for r in self.entry]

    def as_fn(self):
        """Devuelve un callable con la MISMA firma que las estrategias de
        signals/strategies.py, asi el backtest, el torneo y el bot en vivo
        la consumen sin saber que es custom."""
        def _fn(closes: list[float]) -> Proposal | None:
            if not self.entry or not all(r.holds(closes) for r in self.entry):
                return None
            return Proposal(self.name, self.describe(), closes[-1])
        return _fn

    def describe(self) -> str:
        return " y ".join(f"{r.indicator} {r.op} {r.value}" for r in self.entry)


def _path(state_dir: Path) -> Path:
    return Path(state_dir) / "custom_strategies.json"


def load_all(state_dir: Path) -> dict[str, StrategySpec]:
    path = _path(state_dir)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {name: StrategySpec(**spec) for name, spec in raw.items()}


def save(state_dir: Path, spec: StrategySpec) -> None:
    if not spec.name or not spec.name.replace("_", "").isalnum():
        raise ValueError("el nombre solo puede tener letras, numeros y guion bajo")
    if not spec.entry:
        raise ValueError("una estrategia sin condiciones de entrada compraria siempre")
    for r in spec.entry:
        if r.indicator not in INDICATORS:
            raise ValueError(f"indicador desconocido: {r.indicator!r}. Validos: {sorted(INDICATORS)}")
        if r.op not in OPS:
            raise ValueError(f"operador desconocido: {r.op!r}. Validos: {sorted(OPS)}")

    all_specs = load_all(state_dir)
    all_specs[spec.name] = spec
    write_json_atomic(_path(state_dir), {n: asdict(s) for n, s in all_specs.items()})


def delete(state_dir: Path, name: str) -> bool:
    all_specs = load_all(state_dir)
    if name not in all_specs:
        return False
    del all_specs[name]
    write_json_atomic(_path(state_dir), {n: asdict(s) for n, s in all_specs.items()})
    return True
