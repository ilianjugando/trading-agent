from signals.llm_review import LLMReview, _aggregate


def test_majority_buy_wins():
    votes = [
        LLMReview("buy", 0.8, "a"),
        LLMReview("buy", 0.6, "b"),
        LLMReview("hold", 0.0, "c"),
    ]
    result = _aggregate(votes)
    assert result.action == "buy"
    assert result.confidence == 0.7  # average of the two buy votes


def test_single_buy_wins_even_against_a_hold():
    # Any one buy vote wins -- not a majority (changed 2026-09-09: real
    # data showed a >=2/3 majority rule was vetoing candidates that had
    # already cleared the RSI-overbought filter on ordinary model
    # disagreement, not on genuine risk signal).
    votes = [LLMReview("buy", 0.9, "a"), LLMReview("hold", 0.0, "b")]
    result = _aggregate(votes)
    assert result.action == "buy"
    assert result.confidence == 0.9


def test_all_hold():
    votes = [LLMReview("hold", 0.0, "a"), LLMReview("hold", 0.0, "b")]
    assert _aggregate(votes).action == "hold"


def test_single_gemini_only_buy():
    assert _aggregate([LLMReview("buy", 0.75, "a")]).action == "buy"


def test_panel_degraded_when_most_votes_were_api_errors():
    """Seccion 38: 'el panel evaluo y dijo que no' y 'el panel no pudo
    evaluar' llevan al mismo resultado seguro, pero no son el mismo hecho.
    Caso real (2026-09-10, SIG): 2 de 3 votos eran errores de API y la
    alerta mandaba a revisar umbrales del panel."""
    from signals.llm_review import LLMReview, _aggregate

    votes = [
        LLMReview("hold", 0.40, "juicio real"),
        LLMReview("hold", 0.0, "api cayo", errored=True),
        LLMReview("hold", 0.0, "api cayo", errored=True),
    ]
    result = _aggregate(votes)
    assert result.action == "hold"
    assert result.errored, "un panel mayormente caido no es un juicio del panel"


def test_panel_not_degraded_when_votes_are_real():
    from signals.llm_review import LLMReview, _aggregate

    votes = [
        LLMReview("hold", 0.40, "juicio real"),
        LLMReview("hold", 0.30, "juicio real"),
        LLMReview("hold", 0.0, "api cayo", errored=True),
    ]
    result = _aggregate(votes)
    assert result.action == "hold"
    assert not result.errored, "un solo fallo no invalida el juicio del panel"
