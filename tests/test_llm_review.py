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


def test_tie_defaults_to_hold():
    votes = [LLMReview("buy", 0.9, "a"), LLMReview("hold", 0.0, "b")]
    assert _aggregate(votes).action == "hold"


def test_all_hold():
    votes = [LLMReview("hold", 0.0, "a"), LLMReview("hold", 0.0, "b")]
    assert _aggregate(votes).action == "hold"


def test_single_gemini_only_buy():
    assert _aggregate([LLMReview("buy", 0.75, "a")]).action == "buy"
