"""T258-WHATCOULDGOWRONG-AGENT: adversarial pre-trade risk enumeration.

After the numerical score/hard-reject/LLM-verdict pipeline has run, optionally asks Claude
to argue AGAINST the trade — enumerate concrete failure modes using only the context this
service already fetched (regime, signal reasons, research), tagged macro/sector/company/
technical with severity. Same fail-open/opt-in/Redis-API-key conventions as llm_scorer.py.

Deliberately does NOT emit a probability_of_failure number: per the source design doc
(Improvements/AI Trading Platform - Combined Agent Catalog.md, agent 8) and this repo's own
"don't let a rubric that sounds right stay in production unvalidated" discipline, an LLM
narrating "73% chance of failure" is not evidence of a 73% edge — it's evidence the model
followed formatting instructions. The value here is the forced, concrete risk ENUMERATION a
human reads before entering, not an unvalidated confidence number attached to it.

Config keys (in portfolio/decision config):
  risk_check_enabled : bool  (default False — explicit opt-in, same as llm_scoring_enabled)
  risk_check_model    : str  (default "claude-haiku-4-5-20251001")

Fail-open: any error returns None (not an empty list — see risk_agent.py's own note on why
"no risks found" is never a real state this function reports).
API key: read from Redis stockai:admin:claude_api_key (same key llm_scorer.py already uses).
"""
import json
import logging
import re
from datetime import datetime, timezone

import httpx

log = logging.getLogger("de.risk_agent")

_REDIS_CLAUDE_KEY = "stockai:admin:claude_api_key"
_CACHE_TTL_SECONDS = 6 * 3600  # 6 hours — matches llm_scorer.py's cache window

_VALID_CATEGORIES = {"macro", "sector", "company", "technical"}
_VALID_SEVERITIES = {"low", "medium", "high"}

_SYSTEM = """You are a skeptical hedge fund risk manager whose job is to argue AGAINST a
proposed trade. Assume the trade is wrong and identify concrete reasons it could fail, using
ONLY the data supplied — do not invent risks you have no basis for in the given context.
Respond ONLY with valid JSON (no markdown, no explanation outside JSON) in this exact format:
{"risks":[{"category":"macro","severity":"high","note":"<one concise sentence, max 140 chars>"}]}
category must be one of: macro, sector, company, technical.
severity must be one of: low, medium, high.
Identify at least 3 and at most 8 risks. If you genuinely lack any basis for a category
(e.g. no macro data was supplied), omit that category rather than inventing a generic risk."""


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
    return f"de:risk:{symbol}:{style}:{date_part}"


def _build_prompt(
    symbol: str,
    style: str,
    sig_direction: str,
    confidence: float,
    game_plan: dict,
    regime_state: str,
    vix: float | None,
    is_pre_choppy: bool,
    is_pre_risk_off: bool,
    research_rec: str | None,
    research_score: float | None,
    days_to_earnings: int | None,
    volume_z: float | None,
    reasons: dict,
) -> str:
    live = game_plan.get("entry2", game_plan.get("stop", 0) * 1.1)
    stop = game_plan.get("stop", 0)
    tp = game_plan.get("take_profit", 0)

    parts = [
        f"Proposed trade: {sig_direction} {symbol} | Style: {style} | Confidence: {confidence:.0%}",
        f"Game plan: entry~${live:.2f} stop=${stop:.2f} target=${tp:.2f}",
        f"Market regime: {regime_state}" + (f" (VIX {vix:.1f})" if vix else ""),
    ]
    if is_pre_choppy:
        parts.append("Early-warning flag: pre-choppy regime transition detected")
    if is_pre_risk_off:
        parts.append("Early-warning flag: pre-risk-off regime transition detected")
    if research_rec:
        rec_str = f"AI research recommendation: {research_rec}"
        if research_score is not None:
            rec_str += f" (score {research_score:.0f}/100)"
        parts.append(rec_str)
    if days_to_earnings is not None:
        parts.append(f"Days to next earnings: {days_to_earnings}")
    if volume_z is not None:
        parts.append(f"Volume z-score: {volume_z:.2f}")
    for key, label in (
        ("sector_momentum", "Sector momentum"),
        ("squeeze_score", "Short-squeeze score"),
        ("insider_cluster", "Insider cluster activity"),
        ("congress_buy", "Congress buying activity"),
        ("eight_k_flag", "Recent 8-K filing flag"),
        ("eps_revision_direction", "Analyst EPS revision direction"),
        ("inst_change_pct", "Institutional ownership QoQ change %"),
    ):
        val = reasons.get(key)
        if val is not None:
            parts.append(f"{label}: {val}")

    parts.append(
        "\nArgue against this trade. Identify concrete failure modes across macro, sector, "
        "company, and technical dimensions, using only the data above."
    )
    return "\n".join(parts)


async def check_risks(
    symbol: str,
    style: str,
    sig_direction: str,
    confidence: float,
    game_plan: dict,
    regime_state: str,
    regime: dict,
    is_pre_choppy: bool,
    is_pre_risk_off: bool,
    research_rec: str | None,
    research_score: float | None,
    days_to_earnings: int | None,
    volume_z: float | None,
    reasons: dict,
    sig_ts: str | None,
    cfg: dict,
) -> list[dict] | None:
    """Return a list of {category, severity, note} dicts, or None if the check didn't run
    or failed. Never raises — same fail-open contract as llm_scorer.score_with_llm()."""
    if not cfg.get("risk_check_enabled", False):
        return None

    api_key = _get_api_key(cfg)
    if not api_key:
        log.warning("de.risk_agent.no_api_key symbol=%s", symbol)
        return None

    model = cfg.get("risk_check_model", "claude-haiku-4-5-20251001")
    cache_key = _cache_key(symbol, style, sig_ts)

    try:
        r = _redis_client()
        cached = r.get(cache_key)
        if cached:
            data = json.loads(cached)
            log.info("de.risk_agent.cache_hit symbol=%s n_risks=%d", symbol, len(data.get("risks", [])))
            return data.get("risks")
    except Exception as exc:
        log.warning("de.risk_agent.cache_read_failed error=%s", exc)

    prompt = _build_prompt(
        symbol=symbol, style=style, sig_direction=sig_direction, confidence=confidence,
        game_plan=game_plan, regime_state=regime_state, vix=regime.get("vix"),
        is_pre_choppy=is_pre_choppy, is_pre_risk_off=is_pre_risk_off,
        research_rec=research_rec, research_score=research_score,
        days_to_earnings=days_to_earnings, volume_z=volume_z, reasons=reasons or {},
    )

    body = {
        "model": model,
        "max_tokens": 512,
        "temperature": 0.2,
        "system": _SYSTEM,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post("https://api.anthropic.com/v1/messages", headers=headers, json=body)
        if r.status_code != 200:
            log.warning("de.risk_agent.api_error status=%d body=%s", r.status_code, r.text[:200])
            return None
        raw = r.json()["content"][0]["text"].strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.DOTALL).strip()
        data = json.loads(raw)
    except Exception as exc:
        log.warning("de.risk_agent.call_failed symbol=%s error=%s", symbol, exc)
        return None

    raw_risks = data.get("risks", [])
    if not isinstance(raw_risks, list):
        return None

    risks: list[dict] = []
    for item in raw_risks:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category", "")).lower()
        severity = str(item.get("severity", "")).lower()
        note = str(item.get("note", ""))[:200]
        if category not in _VALID_CATEGORIES or severity not in _VALID_SEVERITIES or not note:
            continue
        risks.append({"category": category, "severity": severity, "note": note})

    if not risks:
        log.warning("de.risk_agent.no_valid_risks_parsed symbol=%s", symbol)
        return None

    log.info("de.risk_agent.scored symbol=%s style=%s n_risks=%d", symbol, style, len(risks))

    try:
        rc = _redis_client()
        rc.setex(cache_key, _CACHE_TTL_SECONDS, json.dumps({"risks": risks}))
    except Exception:
        pass

    return risks
