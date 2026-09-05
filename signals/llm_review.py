"""Gemini reviews a computed numeric signal and returns a structured
proposal. This is advisory only -- risk/spend_guard.py and
risk/circuit_breaker.py are the actual gatekeepers and cannot be
overridden by anything returned here.
"""
import json
from dataclasses import dataclass

import google.generativeai as genai

_MODEL_NAME = "gemini-2.5-flash"

_PROMPT_TEMPLATE = """You are a trading signal reviewer, not a trader. You do not place orders.
Given this numeric momentum signal, decide whether it's worth proposing a small BUY.
Be skeptical by default -- most signals are noise. Respond with ONLY a JSON object,
no markdown fences, matching exactly this shape:
{{"action": "buy" or "hold", "confidence": 0.0 to 1.0, "reasoning": "one sentence"}}

Signal:
{signal_json}
"""


@dataclass
class LLMReview:
    action: str  # "buy" or "hold"
    confidence: float
    reasoning: str


def review_signal(api_key: str, signal: dict) -> LLMReview:
    """Fails safe: any parsing/API error returns a 'hold' rather than raising,
    so a Gemini outage or malformed response can only block a trade, never
    force one through."""
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(_MODEL_NAME)
        prompt = _PROMPT_TEMPLATE.format(signal_json=json.dumps(signal))
        response = model.generate_content(prompt)
        text = response.text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(text)

        action = data.get("action")
        confidence = float(data.get("confidence", 0))
        reasoning = str(data.get("reasoning", ""))

        if action not in ("buy", "hold"):
            return LLMReview("hold", 0.0, f"Unrecognized action from model: {action!r}")

        return LLMReview(action=action, confidence=confidence, reasoning=reasoning)
    except Exception as e:
        return LLMReview(action="hold", confidence=0.0, reasoning=f"Review failed, defaulting to hold: {e}")
