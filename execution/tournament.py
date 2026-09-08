"""Shadow-mode strategy tournament: the evidence layer.

Every run, each strategy in the registry is asked what it would buy
across the whole scanned universe. Nothing here places an order -- the
proposals are written to SQLite, and a later run scores them against
what the price actually did. After a few weeks that table answers, with
data instead of opinion, which method is worth trusting.

Shadow mode (record everything, execute nothing) is deliberate: running
four strategies for real would have them competing for the same account
balance, and the winner would partly reflect who got funded first rather
than who was right. Scoring on paper removes that confound and costs
nothing.

SQLite because these are relational records we'll aggregate over
(group by strategy, filter by horizon) -- the JSONL decision log is the
wrong shape for that, and sqlite3 ships with Python.
"""
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from signals.strategies import Proposal, evaluate_all

# How long a proposal is left open before it's scored against the market.
DEFAULT_HORIZON_HOURS = 24

_SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT    NOT NULL,
    pool         TEXT    NOT NULL,
    symbol       TEXT    NOT NULL,
    strategy     TEXT    NOT NULL,
    reason       TEXT    NOT NULL,
    entry_price  REAL    NOT NULL,
    scored_at    TEXT,
    exit_price   REAL,
    return_pct   REAL
);
CREATE INDEX IF NOT EXISTS idx_unscored ON proposals (scored_at, ts);
CREATE INDEX IF NOT EXISTS idx_strategy ON proposals (strategy);
"""


@dataclass
class Standing:
    strategy: str
    scored: int
    open_proposals: int
    win_rate: float | None
    avg_return_pct: float | None
    best_pct: float | None
    worst_pct: float | None


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def record(db_path: Path, pool: str, symbol: str, proposals: list[Proposal], now: datetime | None = None) -> int:
    """Persist what each strategy proposed for one symbol. Returns how
    many rows were written."""
    if not proposals:
        return 0
    ts = (now or datetime.now(timezone.utc)).isoformat()
    with closing(_connect(db_path)) as conn, conn:
        conn.executemany(
            "INSERT INTO proposals (ts, pool, symbol, strategy, reason, entry_price) VALUES (?, ?, ?, ?, ?, ?)",
            [(ts, pool, symbol, p.strategy, p.reason, p.entry_price) for p in proposals],
        )
    return len(proposals)


def record_universe(db_path: Path, pool: str, closes_by_symbol: dict[str, list[float]], now: datetime | None = None) -> int:
    """Run every strategy over every symbol in the scanned universe.

    Deliberately not limited to the symbol the live strategy picked:
    scoring only the momentum pick would teach us about momentum's
    choices and nothing about whether another method would have chosen
    better."""
    total = 0
    for symbol, closes in closes_by_symbol.items():
        total += record(db_path, pool, symbol, evaluate_all(closes), now=now)
    return total


def due_for_scoring(db_path: Path, horizon_hours: int = DEFAULT_HORIZON_HOURS, now: datetime | None = None) -> list[sqlite3.Row]:
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(hours=horizon_hours)).isoformat()
    with closing(_connect(db_path)) as conn:
        return conn.execute(
            "SELECT * FROM proposals WHERE scored_at IS NULL AND ts <= ? ORDER BY ts", (cutoff,)
        ).fetchall()


def score_due(db_path: Path, price_lookup, horizon_hours: int = DEFAULT_HORIZON_HOURS, now: datetime | None = None) -> int:
    """Close out proposals older than the horizon at the current price.

    `price_lookup(symbol) -> float | None` is injected rather than a
    broker being imported here, so this is testable offline and the same
    code serves both pools. A lookup that fails leaves the proposal open
    to be retried next run instead of scoring it at a wrong price."""
    rows = due_for_scoring(db_path, horizon_hours, now)
    if not rows:
        return 0

    scored_at = (now or datetime.now(timezone.utc)).isoformat()
    updates = []
    prices: dict[str, float | None] = {}
    for row in rows:
        symbol = row["symbol"]
        if symbol not in prices:
            try:
                prices[symbol] = price_lookup(symbol)
            except Exception:
                prices[symbol] = None
        price = prices[symbol]
        if not price or row["entry_price"] <= 0:
            continue
        return_pct = (price - row["entry_price"]) / row["entry_price"] * 100
        updates.append((scored_at, price, round(return_pct, 3), row["id"]))

    if updates:
        with closing(_connect(db_path)) as conn, conn:
            conn.executemany(
                "UPDATE proposals SET scored_at = ?, exit_price = ?, return_pct = ? WHERE id = ?", updates
            )
    return len(updates)


def standings(db_path: Path) -> list[Standing]:
    """Each strategy's record so far, best average return first.

    `scored` is the number that matters -- a strategy with 3 closed
    proposals has a win rate, but not one worth acting on."""
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT strategy,
                   COUNT(return_pct)                                    AS scored,
                   SUM(CASE WHEN scored_at IS NULL THEN 1 ELSE 0 END)   AS open_proposals,
                   AVG(return_pct)                                      AS avg_return,
                   SUM(CASE WHEN return_pct > 0 THEN 1.0 ELSE 0 END)    AS wins,
                   MAX(return_pct)                                      AS best,
                   MIN(return_pct)                                      AS worst
            FROM proposals GROUP BY strategy
            """
        ).fetchall()

    out = []
    for r in rows:
        scored = r["scored"] or 0
        out.append(Standing(
            strategy=r["strategy"],
            scored=scored,
            open_proposals=r["open_proposals"] or 0,
            win_rate=round(r["wins"] / scored * 100, 1) if scored else None,
            avg_return_pct=round(r["avg_return"], 3) if scored else None,
            best_pct=round(r["best"], 2) if scored else None,
            worst_pct=round(r["worst"], 2) if scored else None,
        ))
    out.sort(key=lambda s: (s.avg_return_pct is not None, s.avg_return_pct or 0), reverse=True)
    return out


def _main() -> None:
    """`python -m execution.tournament` -- read the table from the terminal."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config.settings import ROOT_DIR

    rows = standings(ROOT_DIR / "state" / "tournament.db")
    if not rows:
        print("Todavia no hay propuestas registradas. Corre el orchestrator primero.")
        return

    print(f"{'estrategia':16} {'cerradas':>9} {'abiertas':>9} {'aciertos':>9} {'promedio':>10} {'mejor':>8} {'peor':>8}")
    print("-" * 76)
    for s in rows:
        pct = lambda v, suffix="%": f"{v:.1f}{suffix}" if v is not None else "--"
        print(
            f"{s.strategy:16} {s.scored:>9} {s.open_proposals:>9} {pct(s.win_rate):>9} "
            f"{pct(s.avg_return_pct):>10} {pct(s.best_pct):>8} {pct(s.worst_pct):>8}"
        )
    print()
    print("'cerradas' es lo unico que da confianza: una estrategia con 3 resultados")
    print("tiene un porcentaje de aciertos, pero no uno en el que valga la pena creer.")


if __name__ == "__main__":
    _main()
