"""T203: LLM reasoning layer for the Decision Engine.

After hard rejects pass and numerical score is computed, optionally calls Claude
haiku with the full signal context and asks for a verdict (BUY/HOLD/SKIP) +
reasoning. Returns a score adjustment (+1 BUY, 0 HOLD, -1 SKIP) and the
reasoning string. Result is cached in Redis for 6 hours by signal date.

Config keys (in portfolio/decision config):
  llm_scoring_enabled : bool  (default False — explicit opt-in)
  llm_score_weight    : int   (default 1 — how many points the LLM verdict adds/subtracts)
  llm_model           : str   (default "claude-haiku-4-5-20251001")

Fail-open: any error returns (0, None) — LLM is advisory, not gate-blocking.
API key: read from Redis stockai:admin:claude_api_key (set in admin settings page).
"""
import json
import logging
import re
from datetime import datetime, timezone

import httpx

log = logging.getLogger("de.llm_scorer")

_REDIS_CLAUDE_KEY = "stockai:admin:claude_api_key"
_CACHE_TTL_SECONDS = 6 * 3600  # 6 hours

_SYSTEM = """You are a senior equity trader reviewing a potential entry signal.
You will receive structured data about a stock signal, its market regime, and AI research summary.
Respond ONLY with valid JSON (no markdown, no explanation outside JSON) in this exact format:
{"verdict":"BUY","confidence":0.75,"reasoning":"<one concise sentence>"}
verdict must be BUY, HOLD, or SKIP.
confidence is 0.0–1.0.
reasoning must be one sentence, max 120 chars."""


def _get_api_key(cfg: dict) -> str:
    try:
        r = _redis_client()
        key = r.get(_REDIS_CLAUDE_KEY) or ""
        if key.strip():
            return key.strip()
    except Exception:
        pass
    return cfg.get("claude_api_key", "")


def _redis_client():
    from common.redis_client import get_redis as _get_pool_redis
    return _get_pool_redis()


def _cache_key(symbol: str, style: str, sig_ts: str | None) -> str:
    date_part = (sig_ts or "")[:10] or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"de:llm:{symbol}:{style}:{date_part}"


def _build_prompt(
    symbol: str,
    style: str,
    sig_direction: str,
    confidence: float,
    ml_prob: float | None,
    game_plan: dict,
    regime_state: str,
    vix: float | None,
    research_rec: str | None,
    research_score: float | None,
    cross_style_buys: int,
    score: int,
    min_score: int,
    breakdown_summary: str,
) -> str:
    # Was game_plan.get("entry", ...) — no producer (aggregator.py's _default_game_plan or
    # build_game_plan) ever sets an "entry" key, only "entry2". This always fell through to the
    # fabricated fallback (stop * 1.1) on every call, feeding the LLM a fictitious entry price
    # and R:R that could diverge significantly from the real game plan.
    live = game_plan.get("entry2", game_plan.get("stop", 0) * 1.1)
    stop = game_plan.get("stop", 0)
    tp = game_plan.get("take_profit", 0)
    rr = round((tp - live) / (live - stop), 2) if stop and live and tp and live != stop else None

    parts = [
        f"Symbol: {symbol} | Style: {style}",
        f"Signal: {sig_direction} | Confidence: {confidence:.0%}" +
            (f" | ML bullish probability: {ml_prob:.0%}" if ml_prob is not None else ""),
        f"Game plan: entry~${live:.2f} stop=${stop:.2f} target=${tp:.2f}" +
            (f" R/R={rr:.1f}x" if rr is not None else ""),
        f"Market regime: {regime_state}" + (f" (VIX {vix:.1f})" if vix else ""),
        f"Numerical score: {score}/{min_score} needed",
        f"Score breakdown: {breakdown_summary}",
    ]
    if cross_style_buys:
        parts.append(f"Cross-style consensus: {cross_style_buys} other style(s) also BUY")
    if research_rec:
        rec_str = f"Research recommendation: {research_rec}"
        if research_score is not None:
            rec_str += f" (score {research_score:.0f}/100)"
        parts.append(rec_str)

    parts.append("\nShould we BUY, HOLD, or SKIP this trade? Give your verdict, confidence, and reasoning.")
    return "\n".join(parts)


async def score_with_llm(
    symbol: str,
    style: str,
    sig_direction: str,
    confidence: float,
    ml_prob: float | None,
    game_plan: dict,
    regime_state: str,
    regime: dict,
    research_rec: str | None,
    research_score: float | None,
    cross_style_buys: int,
    score: int,
    min_score: int,
    score_breakdown: list,
    sig_ts: str | None,
    cfg: dict,
) -> tuple[int, str | None]:
    """Return (score_adjustment, reasoning_or_None). Never raises."""
    if not cfg.get("llm_scoring_enabled", False):
        return 0, None

    api_key = _get_api_key(cfg)
    if not api_key:
        log.warning("de.llm_scorer.no_api_key symbol=%s", symbol)
        return 0, None

    weight = int(cfg.get("llm_score_weight", 1))
    model = cfg.get("llm_model", "claude-haiku-4-5-20251001")
    cache_key = _cache_key(symbol, style, sig_ts)

    # Cache check
    try:
        r = _redis_client()
        cached = r.get(cache_key)
        if cached:
            data = json.loads(cached)
            adj = _verdict_to_adj(data.get("verdict", "HOLD"), weight)
            log.info("de.llm_scorer.cache_hit symbol=%s verdict=%s", symbol, data.get("verdict"))
            return adj, data.get("reasoning")
    except Exception as exc:
        log.warning("de.llm_scorer.cache_read_failed error=%s", exc)

    # Build prompt
    def _pts(s) -> int:
        return s.pts if hasattr(s, "pts") else int(s.get("pts", 0))

    def _layer(s) -> str:
        return s.layer if hasattr(s, "layer") else str(s.get("layer", "?"))

    breakdown_summary = " | ".join(
        f"{_layer(s)} {_pts(s):+d}"
        for s in (score_breakdown or [])
        if _pts(s) != 0
    ) or "no scored dimensions"

    prompt = _build_prompt(
        symbol=symbol, style=style,
        sig_direction=sig_direction, confidence=confidence, ml_prob=ml_prob,
        game_plan=game_plan, regime_state=regime_state, vix=regime.get("vix"),
        research_rec=research_rec, research_score=research_score,
        cross_style_buys=cross_style_buys,
        score=score, min_score=min_score, breakdown_summary=breakdown_summary,
    )

    body = {
        "model": model,
        "max_tokens": 256,
        "temperature": 0.1,
        "system": _SYSTEM,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.post("https://api.anthropic.com/v1/messages", headers=headers, json=body)
        if r.status_code != 200:
            log.warning("de.llm_scorer.api_error status=%d body=%s", r.status_code, r.text[:200])
            return 0, None
        raw = r.json()["content"][0]["text"].strip()
        # Strip markdown if present
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.DOTALL).strip()
        data = json.loads(raw)
    except Exception as exc:
        log.warning("de.llm_scorer.call_failed symbol=%s error=%s", symbol, exc)
        return 0, None

    verdict = data.get("verdict", "HOLD").upper()
    reasoning = (data.get("reasoning") or "")[:200]
    llm_confidence = float(data.get("confidence", 0.5))

    log.info("de.llm_scorer.scored symbol=%s style=%s verdict=%s conf=%.2f reasoning=%s",
             symbol, style, verdict, llm_confidence, reasoning)

    # Cache result
    try:
        rc = _redis_client()
        rc.setex(cache_key, _CACHE_TTL_SECONDS, json.dumps({"verdict": verdict, "reasoning": reasoning}))
    except Exception:
        pass

    return _verdict_to_adj(verdict, weight), reasoning


def _verdict_to_adj(verdict: str, weight: int) -> int:
    return {"BUY": weight, "HOLD": 0, "SKIP": -weight}.get(verdict.upper(), 0)
