import sys
import types

import pandas as pd

from signals.kronos_forecast import forecast


def _synthetic_ohlcv(n: int = 120) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="h")
    return pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0}, index=idx)


def test_forecast_returns_none_when_model_unavailable(monkeypatch):
    # Force the lazy `from model import ...` to fail regardless of whether
    # requirements-kronos.txt happens to be installed in this environment
    # -- forecast() must fail closed either way.
    monkeypatch.setitem(sys.modules, "model", None)
    assert forecast(_synthetic_ohlcv()) is None


def test_forecast_returns_none_on_too_short_input():
    assert forecast(_synthetic_ohlcv(n=10)) is None


def test_forecast_computes_change_and_uncertainty_from_mocked_predictor(monkeypatch):
    class _FakeTokenizer:
        @staticmethod
        def from_pretrained(_id):
            return _FakeTokenizer()

    class _FakeModel:
        @staticmethod
        def from_pretrained(_id):
            return _FakeModel()

    class _FakePredictor:
        def __init__(self, model, tokenizer, max_context=512):
            self._toggle = False

        def predict(self, df, x_timestamp, y_timestamp, pred_len, verbose=True):
            close = 105.0 if self._toggle else 110.0
            self._toggle = True
            return pd.DataFrame({"open": close, "high": close, "low": close, "close": close}, index=y_timestamp)

    fake_module = types.ModuleType("model")
    fake_module.Kronos = _FakeModel
    fake_module.KronosTokenizer = _FakeTokenizer
    fake_module.KronosPredictor = _FakePredictor
    monkeypatch.setitem(sys.modules, "model", fake_module)

    result = forecast(_synthetic_ohlcv(), horizon=5, calls=2)

    assert result is not None
    assert result.model_variant == "NeoQuasar/Kronos-small"
    assert result.predicted_close_horizon == 107.5
    assert result.predicted_change_pct == 7.5  # (107.5 - 100) / 100 * 100
    assert result.uncertainty_pct > 0


def test_forecast_returns_none_when_predictor_raises(monkeypatch):
    class _FakeTokenizer:
        @staticmethod
        def from_pretrained(_id):
            return _FakeTokenizer()

    class _FakeModel:
        @staticmethod
        def from_pretrained(_id):
            return _FakeModel()

    class _FakePredictor:
        def __init__(self, model, tokenizer, max_context=512):
            pass

        def predict(self, *args, **kwargs):
            raise RuntimeError("inference blew up")

    fake_module = types.ModuleType("model")
    fake_module.Kronos = _FakeModel
    fake_module.KronosTokenizer = _FakeTokenizer
    fake_module.KronosPredictor = _FakePredictor
    monkeypatch.setitem(sys.modules, "model", fake_module)

    assert forecast(_synthetic_ohlcv()) is None
