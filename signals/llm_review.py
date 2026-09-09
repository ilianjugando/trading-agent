"""A panel of models reviews a computed numeric signal and returns a joint
proposal. This is advisory only -- risk/spend_guard.py and
risk/circuit_breaker.py are the actual gatekeepers and cannot be
overridden by anything returned here.
"""
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib import request as urlrequest

from google import genai

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

# El prompt anterior pedia escepticismo por defecto "porque la mayoria de
# las senales son ruido". Estaba escrito cuando cada aprobacion significaba
# apostar el 20% del pool, y con esa apuesta el escepticismo era correcto.
# Ahora el sizing es por conviccion (execution/sizing.py) y una posicion
# tipica va de 0,5% a 3%: se le estaba pidiendo al panel que evaluara una
# decision distinta de la que el sistema realmente toma. Medido: con el
# prompt viejo, 5 candidatos con esperanza positiva y score de hasta 71/100
# recibieron 0/3 votos de compra, con confianzas altas y genuinas -- el
# panel no estaba roto, estaba respondiendo bien a la pregunta equivocada.
_PROMPT_TEMPLATE = """You are a trading signal reviewer for a portfolio that makes MANY SMALL
asymmetric bets. You do not place orders; you judge whether this specific bet is worth taking.

{position_context}

How to judge this:
- You do NOT need a high probability of success. A bet that wins 35% of the time at 3:1
  reward-to-risk has positive expectancy and is worth taking. Judge the expectancy, not
  the certainty.
- The `asymmetry` block is measured from this asset's own price history, not assumed:
  `expected_value_pct` is the per-trade mathematical expectancy, `win_prob` is the measured
  hit rate, `reward_risk` is target over stop, and `sample_size` tells you how much weight
  that evidence deserves.
- APPROVE when expectancy is positive and the thesis holds together.
- REJECT when the data contradicts itself, the sample is too small to mean anything, the
  asset looks illiquid or manipulated, or the edge is too thin for the uncertainty around it.

Be skeptical of bad data and broken theses -- not of taking a calculated risk with a small
position. Refusing every bet is itself a decision, and a losing one.

Respond with ONLY a JSON object, no markdown fences, matching exactly this shape:
{{"action": "buy" or "hold", "confidence": 0.0 to 1.0, "reasoning": "one sentence"}}

Signal:
{signal_json}
"""

_DEFAULT_POSITION_CONTEXT = (
    "Position sizing: if approved this becomes a small position, capped well below the "
    "portfolio's per-trade limit. A single loss here is designed to be survivable."
)


@dataclass
class LLMReview:
    action: str  # "buy" or "hold"
    confidence: float
    reasoning: str
    # True only for the fail-safe default (API error, malformed response) --
    # never a real model judgment. Without this, a silently-failed vote and
    # a genuine hold(0.00) are byte-identical in the aggregate tally, which
    # already cost real diagnostic time once this session at the whole-panel
    # level (see llm_review.py's model-selection comments above); this is
    # the same failure mode at the single-vote level.
    errored: bool = False


def _parse_review(text: str) -> LLMReview:
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    data = json.loads(text)
    action = data.get("action")
    confidence = float(data.get("confidence", 0))
    reasoning = str(data.get("reasoning", ""))
    if action not in ("buy", "hold"):
        return LLMReview("hold", 0.0, f"Unrecognized action from model: {action!r}", errored=True)
    return LLMReview(action, confidence, reasoning)


def _review_gemini(api_key: str, prompt: str) -> LLMReview:
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(model=_GEMINI_MODEL, contents=prompt)
        return _parse_review(response.text)
    except Exception as e:
        return LLMReview("hold", 0.0, f"gemini failed, defaulting to hold: {e}", errored=True)


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
        return LLMReview("hold", 0.0, f"{model_name} failed, defaulting to hold: {e}", errored=True)


def _aggregate(votes: list[LLMReview]) -> LLMReview:
    """Any single buy vote wins -- not a majority. This was a deliberate,
    data-backed change (2026-09-09): across 67 real crypto review cycles
    (225 individual votes), only 2 votes ever said buy and never more than
    1 in the same cycle, so a >=2/3 majority rule meant momentum candidates
    that already survived the RSI-overbought filter were still getting
    vetoed by ordinary model-to-model disagreement -- the panel and the
    filter were both working as designed, this was a calibration choice,
    not a bug (see llm_review.py's model-selection comments and
    orchestrator.py's RSI-walk comment for the two real bugs that were
    fixed first, before this threshold was ever touched). A hold vote
    still fails safe -- it just no longer outvotes a real buy conviction
    from any one voter. review.confidence < 0.6 in orchestrator.py is a
    separate, untouched gate."""
    buys = [v for v in votes if v.action == "buy"]
    # A trailing "!" marks a fail-safe default (API error / bad response),
    # never a real judgment -- without it, a silently-failed vote and a
    # genuine low-confidence hold render identically in decisions.log.
    tally = "; ".join(f"{v.action}({v.confidence:.2f}{'!' if v.errored else ''})" for v in votes)
    error_count = sum(1 for v in votes if v.errored)
    note = f" ({error_count} fallo{'s' if error_count != 1 else ''} de API)" if error_count else ""
    if not buys:
        return LLMReview("hold", 0.0, f"panel {len(buys)}/{len(votes)} buy{note}: {tally}")
    avg_confidence = sum(v.confidence for v in buys) / len(buys)
    return LLMReview("buy", avg_confidence, f"panel {len(buys)}/{len(votes)} buy{note}: {tally}")


def review_signal(gemini_api_key: str, nvidia_api_key: str, signal: dict,
                  position_context: str | None = None) -> LLMReview:
    """Fails safe: Gemini plus every configured NVIDIA Build model votes
    buy/hold independently (any API error counts as a hold vote); see
    _aggregate for how the panel's votes become one decision. Pass an empty
    nvidia_api_key to fall back to Gemini alone.

    `position_context` describes how big the resulting position would
    actually be. It matters: the same signal deserves a different answer
    when it means 0.5% of the portfolio than when it means 20%."""
    prompt = _PROMPT_TEMPLATE.format(
        signal_json=json.dumps(signal),
        position_context=position_context or _DEFAULT_POSITION_CONTEXT,
    )

    jobs = [lambda: _review_gemini(gemini_api_key, prompt)]
    if nvidia_api_key:
        jobs += [lambda m=m: _review_nvidia(nvidia_api_key, m, prompt) for m in _NVIDIA_MODELS]

    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = [pool.submit(job) for job in jobs]
        votes = [f.result() for f in futures]

    return _aggregate(votes)
