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
