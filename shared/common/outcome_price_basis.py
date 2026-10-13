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
#: Actions that change the share count, and therefore the meaning of a raw price.
SHARE_COUNT_ACTIONS = ("split", "stock_dividend")
#: The ONLY action type known to leave a price-return figure intact. An ALLOWLIST, not a
#: blocklist of share-count types: a spinoff, rights issue or any type this module has never
#: heard of moves the price for a reason that is not a return, so it stays excluded until
#: someone writes the adjustment for it. (The first cut of AUD-PRICERISK-DIVIDEND blocklisted
#: share-count types instead, and a spinoff window came back usable.)
PRICE_NEUTRAL_ACTIONS = ("cash_dividend",)


def _get(obj, name, default=None):
    if obj is None:
        return default
    return obj.get(name, default) if hasattr(obj, "get") else getattr(obj, name, default)


def resolve_window(bars, actions, coverage, *, start: date, end: date) -> dict:
    """Return coherent split-adjusted `(date, close)` bars plus evidence status."""
    selected = [(d, float(c)) for d, c in bars if start <= d <= end and c is not None]
    if not selected:
        return {"prices": [], "basis": BASIS, "method": METHOD,
                "evidence_status": UNVERIFIED, "limiting_factor": "no_prices",
                "reason": "no price bars in outcome window"}
    evidenced_from = _get(coverage, "evidenced_from")
    evidenced_to = _get(coverage, "evidenced_to")
    covered = bool(evidenced_from and evidenced_to
                   and evidenced_from <= selected[0][0] and evidenced_to >= selected[-1][0])
    if not covered:
        # FRESHNESS, not completeness. This one is fixable by re-fetching: the source was simply
        # never asked about these dates. Named separately because the two unverified cases have
        # completely different remedies and conflating them makes a refresh look like a fix for
        # both — it is not. See `best_achievable_status`.
        return {"prices": [], "basis": BASIS, "method": METHOD,
                "evidence_status": UNVERIFIED, "limiting_factor": "coverage_span",
                "reason": "corporate-action evidence does not span the outcome window"}

    window_actions = [a for a in actions
                      if start <= _get(a, "ex_date") <= end]
    splits = [a for a in window_actions
              if _get(a, "action_type") in SHARE_COUNT_ACTIONS]
    if any(not _get(a, "split_ratio") for a in splits):
        return {"prices": [], "basis": BASIS, "method": METHOD,
                "evidence_status": UNVERIFIED, "limiting_factor": "unapplied_action",
                "reason": "share-count action has no recorded ratio"}
    # Anything else that is not price-neutral — a spinoff, a rights issue, an unknown type —
    # has no adjustment here, so the window cannot be presented as adjusted. Before 2026-10-13
    # this branch applied splits and silently passed everything else through as `adjusted`.
    unresolved = sorted({str(_get(a, "action_type")) for a in window_actions
                         if _get(a, "action_type") not in SHARE_COUNT_ACTIONS
                         and _get(a, "action_type") not in PRICE_NEUTRAL_ACTIONS})
    if unresolved:
        return {"prices": [], "basis": BASIS, "method": METHOD,
                "evidence_status": UNVERIFIED, "limiting_factor": "unapplied_action",
                "reason": f"no adjustment exists for: {', '.join(unresolved)}"}

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
            # COMPLETENESS, not freshness. Re-fetching cannot improve this one: the span is
            # covered and the source still publishes no guarantee that its list is exhaustive.
            "limiting_factor": None if status == VERIFIED else "source_completeness",
            "reason": ("source documents complete action history" if status == VERIFIED else
                       "source response has no documented completeness guarantee")}


# Observation-level outcomes of applying the contract above. `resolve_window` answers "can this
# window be adjusted?"; these answer the question a CALLER has, which is "may I use this number,
# and what is it a number of?".
ADJUSTED = "adjusted"
UNADJUSTED_NO_KNOWN_ACTION = "unadjusted_no_known_action"
KNOWN_CORRUPT = "known_corrupt"


def classify_observation_window(resolved, actions, *, start: date, end: date) -> dict:
    """Decide whether one forward return over [start, end] may be used, and on what basis.

    AQ04 residual (2026-10-12). `resolve_window` returns no prices whenever evidence does not
    span the window, which collapses two genuinely different situations into one empty result:

      * a corporate action is RECORDED inside the window and could not be applied — the raw
        return is known to be wrong, because a 2:1 split reads as a 50% loss; and
      * nothing is recorded for the window at all — the raw return is unverified, which is not
        the same as wrong, and discarding it would silently shrink the population a calibration
        is fitted on.

    Treating those alike would either throw away most of the sample or admit a return that is
    definitely corrupt. So the first is refused and the second is kept and LABELLED, and the
    caller reports both counts rather than quietly resolving the ambiguity in either direction.

    Note the asymmetry is deliberate: "no action recorded" is not evidence that no action
    occurred, which is exactly why the result stays `unverified` and cannot reach a published
    rate. It is good enough to fit a weight on and not good enough to quote.
    """
    # Cash dividends are NAMED, never silently dropped. They do not corrupt a price-return
    # figure — the drop on the ex-date is real — but a caller measuring what a HOLDER lost may
    # want to add them back, and a large special dividend can look like a decline. Which basis
    # to apply is the caller's decision; hiding the fact is not an option either way.
    dividends = [{"ex_date": _get(a, "ex_date"), "cash_amount": _get(a, "cash_amount")}
                 for a in actions if start <= _get(a, "ex_date") <= end
                 and _get(a, "action_type") == "cash_dividend"]
    if resolved.get("prices"):
        return {"usable": True, "basis": resolved.get("basis", BASIS),
                "adjustment": ADJUSTED,
                "evidence_status": resolved.get("evidence_status", PROVISIONAL),
                "reason": resolved.get("reason", ""), "cash_dividends": dividends}

    # Every action in the window corrupts the figure EXCEPT a cash dividend. The basis above is
    # price return, `resolve_window` never adjusts for one, and its ex-date drop is real and
    # small; counting it here made AVGO "unmeasurable" through its whole 2026-10 decline
    # (AUD-PRICERISK-DIVIDEND). Spinoffs, rights issues and unknown types stay excluded — see
    # PRICE_NEUTRAL_ACTIONS for why this is an allowlist.
    in_window = [a for a in actions if start <= _get(a, "ex_date") <= end
                 and _get(a, "action_type") not in PRICE_NEUTRAL_ACTIONS]
    if in_window:
        kinds = sorted({str(_get(a, "action_type")) for a in in_window})
        return {"usable": False, "basis": None, "adjustment": KNOWN_CORRUPT,
                "evidence_status": UNVERIFIED,
                "reason": f"corporate action in window could not be applied: {', '.join(kinds)}",
                "cash_dividends": dividends}

    return {"usable": True, "basis": "raw_price", "adjustment": UNADJUSTED_NO_KNOWN_ACTION,
            "evidence_status": UNVERIFIED,
            "reason": ("no price-affecting action recorded in window; completeness unverified"
                       + (f"; {len(dividends)} cash dividend(s) in window, not adjusted"
                          if dividends else "")),
            "cash_dividends": dividends}


def best_achievable_status(completeness_basis: str | None) -> str:
    """The strongest status this SOURCE can ever support, however fresh its coverage is.

    Freshness and completeness are different limits with different remedies, and the difference
    is easy to lose. Re-fetching a stale span makes a window COMPUTABLE — unverified becomes
    provisional. It does not make it VERIFIED, because verified means the source documents that
    its action list is exhaustive, and yfinance publishes no such guarantee. So a refresh
    campaign against a `response_only` source raises every window to provisional and stops
    there, by design rather than by failure.

    Stated as a function so a surface can say which ceiling applies instead of implying that a
    figure is one refresh away from verified when it is not.
    """
    return VERIFIED if completeness_basis == "source_guarantee" else PROVISIONAL
