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


# --- Traducir una descripcion en castellano a reglas -------------------

_TRANSLATE_PROMPT = """Convertis la descripcion de una estrategia de trading en reglas de compra.

Indicadores disponibles (usa EXACTAMENTE estos nombres):
- rsi: RSI de 14 periodos, 0 a 100
- sma_trend: tendencia, valores "up" | "down" | "flat"
- change_pct: variacion porcentual de la ultima barra
- change_5_pct: variacion porcentual de las ultimas 5 barras
- above_high_20: true si rompe el maximo de las ultimas 20 barras
- below_low_20: true si rompe el minimo de las ultimas 20 barras

Operadores: < <= > >= == !=

Reglas:
- Devolve SOLO JSON, sin explicaciones ni markdown.
- Formato: {{"entry": [{{"indicator": "...", "op": "...", "value": ...}}], "resumen": "..."}}
- TODAS las condiciones se combinan con Y logico.
- No inventes indicadores: si la descripcion pide algo que no esta en la
  lista, aproximalo con los que hay o ignoralo.
- La SALIDA no se define: el sistema usa su propia medicion de stop y
  objetivo. No devuelvas nada sobre salidas.
- "resumen" es una frase corta en castellano de lo que hace la estrategia.

Descripcion: {description}"""


def from_description(description: str, gemini_api_key: str, nvidia_api_key: str = "") -> dict:
    """Descripcion en castellano -> reglas propuestas.

    Devuelve {"entry": [...], "resumen": "..."} para que el usuario lo
    revise ANTES de guardarlo. A proposito no guarda nada: un modelo
    traduciendo texto libre a algo que despues opera con plata real tiene
    que pasar por una confirmacion humana, igual que el panel nunca
    coloca una orden por su cuenta.
    """
    from signals.llm_review import _GEMINI_MODEL, genai

    prompt = _TRANSLATE_PROMPT.format(description=description)
    client = genai.Client(api_key=gemini_api_key)
    text = client.models.generate_content(model=_GEMINI_MODEL, contents=prompt).text
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    data = json.loads(text)

    # Se valida contra el mismo whitelist que usa save(): lo que el modelo
    # devuelve es una PROPUESTA, no algo en lo que se confie.
    rules = []
    for r in data.get("entry", []):
        ind, op = r.get("indicator"), r.get("op")
        if ind in INDICATORS and op in OPS:
            rules.append(Rule(ind, op, r.get("value")))
    if not rules:
        raise ValueError("no se pudo traducir esa descripcion a reglas conocidas")
    return {"entry": [asdict(r) for r in rules], "resumen": str(data.get("resumen", ""))[:200]}
