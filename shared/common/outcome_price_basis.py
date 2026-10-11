"""One corporate-action evidence contract for forward-return evaluators.

The numeric return basis is split-adjusted price. A source response without a documented
completeness guarantee is provisional; missing span evidence is unverified. Computation and
publication evidence remain separate questions.
"""
from __future__ import annotations

from datetime import date

BASIS = "split_adjusted_price"
METHOD = "cumulative_split_factor_v1"
VERIFIED = "verified"
PROVISIONAL = "provisional"
UNVERIFIED = "unverified"


def _get(obj, name, default=None):
    if obj is None:
        return default
    return obj.get(name, default) if hasattr(obj, "get") else getattr(obj, name, default)


def resolve_window(bars, actions, coverage, *, start: date, end: date) -> dict:
    """Return coherent split-adjusted `(date, close)` bars plus evidence status."""
    selected = [(d, float(c)) for d, c in bars if start <= d <= end and c is not None]
    if not selected:
        return {"prices": [], "basis": BASIS, "method": METHOD,
                "evidence_status": UNVERIFIED, "reason": "no price bars in outcome window"}
    evidenced_from = _get(coverage, "evidenced_from")
    evidenced_to = _get(coverage, "evidenced_to")
    covered = bool(evidenced_from and evidenced_to
                   and evidenced_from <= selected[0][0] and evidenced_to >= selected[-1][0])
    if not covered:
        return {"prices": [], "basis": BASIS, "method": METHOD,
                "evidence_status": UNVERIFIED,
                "reason": "corporate-action evidence does not span the outcome window"}

    window_actions = [a for a in actions
                      if start <= _get(a, "ex_date") <= end]
    splits = [a for a in window_actions
              if _get(a, "action_type") in ("split", "stock_dividend")]
    if any(not _get(a, "split_ratio") for a in splits):
        return {"prices": [], "basis": BASIS, "method": METHOD,
                "evidence_status": UNVERIFIED,
                "reason": "share-count action has no recorded ratio"}

    adjusted = []
    for bar_date, close in selected:
        factor = 1.0
        for action in splits:
            if bar_date < _get(action, "ex_date"):
                factor /= float(_get(action, "split_ratio"))
        adjusted.append((bar_date, close * factor))

    completeness = _get(coverage, "completeness_basis", "response_only")
    status = VERIFIED if completeness == "source_guarantee" else PROVISIONAL
    return {"prices": adjusted, "basis": BASIS, "method": METHOD,
            "evidence_status": status,
            "reason": ("source documents complete action history" if status == VERIFIED else
                       "source response has no documented completeness guarantee")}
