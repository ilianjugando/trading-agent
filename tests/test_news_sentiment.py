from datetime import datetime, timedelta, timezone

from signals.news_sentiment import _count_matches, fetch_sentiment


def test_count_matches_is_case_insensitive_and_word_bounded():
    assert _count_matches("Stock will SURGE after strong earnings beat", ["surge", "beat"]) == 2
    # "beat" must not match inside "heartbeat", and "surge" (singular) must
    # not match "surges" -- word-boundary regex, not substring.
    assert _count_matches("a heartbeat away", ["beat"]) == 0
    assert _count_matches("shares surges higher", ["surge"]) == 0


def _article(title: str, hours_ago: float = 1.0) -> dict:
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")
    return {"content": {"title": title, "summary": "", "pubDate": ts}}


def test_fetch_sentiment_scores_bullish_and_bearish(monkeypatch):
    articles = [_article("Stock surges after beat"), _article("Shares plunge on downgrade")]

    class _FakeTicker:
        def __init__(self, symbol):
            pass

        @property
        def news(self):
            return articles

    monkeypatch.setattr("signals.news_sentiment.yf.Ticker", _FakeTicker)

    result = fetch_sentiment("TEST")
    assert result.article_count == 2
    assert result.sentiment_score == 0.0  # one bullish word, one bearish word -- cancels out
    assert result.mover_mentions == 0
    assert result.hours_since_latest is not None


def test_fetch_sentiment_detects_mover_mentions(monkeypatch):
    articles = [_article("Elon Musk says demand remains strong")]

    class _FakeTicker:
        def __init__(self, symbol):
            pass

        @property
        def news(self):
            return articles

    monkeypatch.setattr("signals.news_sentiment.yf.Ticker", _FakeTicker)

    result = fetch_sentiment("TSLA")
    # Both the "musk" and "elon musk" list entries match this headline --
    # mover_mentions is a mention-count, not a distinct-person count, so 2
    # is correct here, not a bug.
    assert result.mover_mentions == 2


def test_fetch_sentiment_excludes_stale_articles(monkeypatch):
    articles = [_article("Old bearish crash news", hours_ago=200)]  # older than default 72h lookback

    class _FakeTicker:
        def __init__(self, symbol):
            pass

        @property
        def news(self):
            return articles

    monkeypatch.setattr("signals.news_sentiment.yf.Ticker", _FakeTicker)

    result = fetch_sentiment("TEST")
    assert result.article_count == 0
    assert result.sentiment_score == 0.0
    assert result.hours_since_latest is None


def test_fetch_sentiment_raises_on_failure(monkeypatch):
    class _FakeTicker:
        def __init__(self, symbol):
            pass

        @property
        def news(self):
            raise RuntimeError("network blew up")

    monkeypatch.setattr("signals.news_sentiment.yf.Ticker", _FakeTicker)

    try:
        fetch_sentiment("TEST")
        assert False, "expected an exception, not a silently-swallowed None"
    except RuntimeError:
        pass
