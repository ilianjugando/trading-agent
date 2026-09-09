import json

import pytest

from signals.opportunity_scanner import (
    REJECT_INSUFFICIENT_DATA,
    REJECT_NEGATIVE_EV,
    scan,
)


def _rising(n: int = 120, start: float = 100.0, step: float = 0.5) -> list[float]:
    return [start + i * step for i in range(n)]


def _falling(n: int = 120) -> list[float]:
    return [200.0 - i * 0.5 for i in range(n)]


def test_rejects_short_history_with_a_reason():
    result = scan({"CORTO": {"closes": [1.0, 2.0, 3.0]}})
    assert result.scanned == 1
    assert result.shortlist == []
    assert result.rejected[0].rejected_because == REJECT_INSUFFICIENT_DATA
    assert result.rejection_summary[REJECT_INSUFFICIENT_DATA] == 1


def test_rejects_negative_expectancy_with_the_numbers_attached():
    """El motivo del rechazo tiene que traer el valor que lo causo -- sin
    eso es imposible saber si el sistema fue razonable o demasiado duro."""
    result = scan({"BAJISTA": {"closes": _falling()}})
    assert result.shortlist == []
    rejected = result.rejected[0]
    assert rejected.rejected_because == REJECT_NEGATIVE_EV
    assert "ev_pct" in rejected.metrics


def test_accepts_and_scores_a_positive_expectancy_asset():
    result = scan({"ALCISTA": {"closes": _rising()}})
    assert len(result.shortlist) == 1
    candidate = result.shortlist[0]
    assert candidate.accepted
    assert candidate.score > 0
    assert candidate.asymmetry is not None
    assert "score_breakdown" in candidate.metrics


def test_shortlist_is_ranked_best_first():
    result = scan({
        "FUERTE": {"closes": _rising(step=1.0)},
        "DEBIL": {"closes": _rising(step=0.05)},
    })
    scores = [c.score for c in result.shortlist]
    assert scores == sorted(scores, reverse=True)


def test_overbought_is_penalised_not_eliminated():
    """El cambio central del rediseno: un RSI alto baja el score, no
    descalifica. Antes un RSI>=70 eliminaba el candidato de plano."""
    parabolic = _rising(n=100) + [200.0 + i * 12 for i in range(20)]
    result = scan({"PARABOLICO": {"closes": parabolic}})
    assert len(result.shortlist) == 1, "un activo extendido debe seguir compitiendo"
    breakdown = result.shortlist[0].metrics["score_breakdown"]
    assert breakdown["penalizacion"] < 0


def test_scans_the_whole_universe_not_just_the_first():
    """Lo que rompia el sistema viejo: miraba 243 activos y evaluaba 1."""
    universe = {f"SYM{i}": {"closes": _rising(step=0.3 + i * 0.05)} for i in range(25)}
    result = scan(universe)
    assert result.scanned == 25
    assert len(result.shortlist) + len(result.rejected) == 25


def test_metadata_is_carried_through_to_the_payload():
    result = scan({"X": {"closes": _rising(), "size_bucket": "micro", "change_24h_pct": 9.1}})
    metrics = result.shortlist[0].metrics
    assert metrics["size_bucket"] == "micro"
    assert metrics["change_24h_pct"] == 9.1


def test_as_metrics_is_json_serializable_and_has_the_required_counters():
    result = scan({
        "BUENO": {"closes": _rising()},
        "MALO": {"closes": _falling()},
        "CORTO": {"closes": [1.0]},
    })
    metrics = result.as_metrics()
    for key in ("assets_scanned", "assets_shortlisted", "assets_rejected",
                "rejection_reasons", "top_candidates", "rejected_symbols"):
        assert key in metrics
    assert metrics["assets_scanned"] == 3
    json.dumps(metrics)


def test_rejected_symbols_are_grouped_by_reason_for_drilldown():
    result = scan({
        "BUENO": {"closes": _rising()},
        "MALO": {"closes": _falling()},
        "CORTO": {"closes": [1.0]},
    })
    grouped = result.as_metrics()["rejected_symbols"]
    assert grouped[REJECT_NEGATIVE_EV] == ["MALO"]
    assert grouped[REJECT_INSUFFICIENT_DATA] == ["CORTO"]


def test_raises_on_invalid_input_shape():
    with pytest.raises(TypeError):
        scan(["no", "es", "un", "dict"])
