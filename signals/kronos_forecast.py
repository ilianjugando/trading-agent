"""Additive forecast signal via a vendored copy of shiyu-coder/Kronos
(vendor/kronos/, MIT licensed) -- a small foundation model for K-line
(candlestick) forecasting. Advisory only, same as RSI/SMA/volatility in
indicators.py: this never gates a trade by itself, it's one more field
attached to the signal JSON the LLM panel sees.

Heavy, optional dependency (torch et al. -- see requirements-kronos.txt,
not installed by default). Everything here is wrapped so ANY failure --
not installed, no cached weights, no network, bad input, inference
error -- returns None and the caller just omits this key. Same
fail-toward-inaction pattern as _review_gemini in llm_review.py.
"""
import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, stdev

import pandas as pd

_VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor" / "kronos"
_TOKENIZER_ID = "NeoQuasar/Kronos-Tokenizer-base"
_MODEL_ID = "NeoQuasar/Kronos-small"


@dataclass
class KronosForecast:
    predicted_close_horizon: float
    predicted_change_pct: float
    uncertainty_pct: float
    model_variant: str


def forecast(ohlcv: pd.DataFrame, horizon: int = 12, calls: int = 3) -> KronosForecast | None:
    """`ohlcv` needs open/high/low/close columns (any case) and a
    DatetimeIndex, at least 100 rows. Forecasts `horizon` bars ahead.

    Runs `calls` independent forecasts and reports their mean/spread as
    a rough uncertainty band -- Kronos's own `sample_count` parameter
    already averages multiple sampled paths *inside* a single predict()
    call, so repeating the call (not raising sample_count) is what
    actually varies the result."""
    try:
        if len(ohlcv) < 100:
            return None

        sys.path.insert(0, str(_VENDOR_DIR))
        from model import Kronos, KronosPredictor, KronosTokenizer

        tokenizer = KronosTokenizer.from_pretrained(_TOKENIZER_ID)
        model = Kronos.from_pretrained(_MODEL_ID)
        predictor = KronosPredictor(model, tokenizer, max_context=512)

        df = ohlcv.rename(columns=str.lower)[["open", "high", "low", "close"]].tail(400)
        x_timestamp = pd.Series(pd.to_datetime(df.index))
        freq = x_timestamp.diff().median()
        last_ts = x_timestamp.iloc[-1]
        y_timestamp = pd.Series([last_ts + freq * (i + 1) for i in range(horizon)])

        last_close = float(df["close"].iloc[-1])
        horizon_closes = []
        for _ in range(calls):
            pred_df = predictor.predict(
                df=df, x_timestamp=x_timestamp, y_timestamp=y_timestamp, pred_len=horizon, verbose=False
            )
            horizon_closes.append(float(pred_df["close"].iloc[-1]))

        avg_close = mean(horizon_closes)
        spread_pct = (stdev(horizon_closes) / avg_close * 100) if len(horizon_closes) > 1 else 0.0

        return KronosForecast(
            predicted_close_horizon=round(avg_close, 4),
            predicted_change_pct=round((avg_close - last_close) / last_close * 100, 2),
            uncertainty_pct=round(spread_pct, 2),
            model_variant=_MODEL_ID,
        )
    except Exception:
        return None
