"""A panel of models reviews a computed numeric signal and returns a joint
proposal. This is advisory only -- risk/spend_guard.py and
risk/circuit_breaker.py are the actual gatekeepers and cannot be
overridden by anything returned here.
"""
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib import request as urlrequest

import google.generativeai as genai

# "latest" alias, not a dated snapshot -- gemini-2.5-flash was retired to
# existing users only (404) partway through this project; an alias that
# Google keeps pointed at their current model avoids repeating this.
# Specifically the "lite" line, not plain "-flash-latest": that resolved
# to gemini-3.8-flash, whose free tier is a mere 20 requests/day -- far
# too low for a panel called every 15min (confirmed via a live 429:
# "limit: 20, model: gemini-3.8-flash"). Lite models get a much more
# generous free daily quota.
_GEMINI_MODEL = "gemini-flash-lite-latest"

# One NVIDIA Build API key gives access to all of these (https://build.nvidia.com),
# but "listed in the catalog" != "entitled to this account" -- meta/llama-3.1-70b-instruct,
# mistralai/mixtral-8x22b-instruct-v0.1, and deepseek-ai/deepseek-v4-pro-0813 (used
# previously) all started 404/410ing without warning; every model here was verified live
# against this account before being added, not picked from the catalog by name alone.
_NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
_NVIDIA_MODELS = [
    "nvidia/nemotron-3-super-120b-a12b",
    "openai/gpt-oss-20b",
]

# Reasoning models emit chain-of-thought (a separate reasoning_content field)
# before the actual answer -- at the old max_tokens=200 that alone could exhaust
# the budget and leave `content` truncated/empty. Where the model honors a
# thinking-off toggle, use it (cheaper, faster, and removes the truncation risk
# outright); max_tokens below is raised regardless as a second line of defense.
_NVIDIA_EXTRA_BODY = {
    "nvidia/nemotron-3-super-120b-a12b": {"chat_template_kwargs": {"thinking": False}},
}

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


def _parse_review(text: str) -> LLMReview:
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    data = json.loads(text)
    action = data.get("action")
    confidence = float(data.get("confidence", 0))
    reasoning = str(data.get("reasoning", ""))
    if action not in ("buy", "hold"):
        return LLMReview("hold", 0.0, f"Unrecognized action from model: {action!r}")
    return LLMReview(action, confidence, reasoning)


def _review_gemini(api_key: str, prompt: str) -> LLMReview:
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(_GEMINI_MODEL)
        return _parse_review(model.generate_content(prompt).text)
    except Exception as e:
        return LLMReview("hold", 0.0, f"gemini failed, defaulting to hold: {e}")


def _review_nvidia(api_key: str, model_name: str, prompt: str) -> LLMReview:
    try:
        body = json.dumps({
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 500,
            **_NVIDIA_EXTRA_BODY.get(model_name, {}),
        }).encode()
        req = urlrequest.Request(
            _NVIDIA_URL, data=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        with urlrequest.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
        return _parse_review(data["choices"][0]["message"]["content"])
    except Exception as e:
        return LLMReview("hold", 0.0, f"{model_name} failed, defaulting to hold: {e}")


def _aggregate(votes: list[LLMReview]) -> LLMReview:
    """Majority vote wins; a tie or non-majority defaults to hold (fails
    safe, same as any single voter erroring out -- a split panel can only
    block a trade, never force one)."""
    buys = [v for v in votes if v.action == "buy"]
    tally = "; ".join(f"{v.action}({v.confidence:.2f})" for v in votes)
    if len(buys) * 2 <= len(votes):
        return LLMReview("hold", 0.0, f"panel {len(buys)}/{len(votes)} buy: {tally}")
    avg_confidence = sum(v.confidence for v in buys) / len(buys)
    return LLMReview("buy", avg_confidence, f"panel {len(buys)}/{len(votes)} buy: {tally}")


def review_signal(gemini_api_key: str, nvidia_api_key: str, signal: dict) -> LLMReview:
    """Fails safe: Gemini plus every configured NVIDIA Build model votes
    buy/hold independently (any API error counts as a hold vote); see
    _aggregate for how the panel's votes become one decision. Pass an empty
    nvidia_api_key to fall back to Gemini alone."""
    prompt = _PROMPT_TEMPLATE.format(signal_json=json.dumps(signal))

    jobs = [lambda: _review_gemini(gemini_api_key, prompt)]
    if nvidia_api_key:
        jobs += [lambda m=m: _review_nvidia(nvidia_api_key, m, prompt) for m in _NVIDIA_MODELS]

    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = [pool.submit(job) for job in jobs]
        votes = [f.result() for f in futures]

    return _aggregate(votes)
