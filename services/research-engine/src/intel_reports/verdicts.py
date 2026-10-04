"""The report's three verdicts, derived from the fields the report actually holds.

NO ORM IMPORT, DELIBERATELY. These are pure functions over a dict of `Field`s, and keeping them
out of the module that imports `db` is what lets them be tested directly. The service conftest
stubs `db` as a plain module, so anything importing `db.models` cannot be imported from a test
at all — a helper buried in `generators` is reachable only through a database fixture, which is
how a behavioural assertion quietly becomes a source-text one.
"""
from __future__ import annotations

from intelligence.report_contract import FieldState


def forward_verdict(fields: dict) -> str:
    """THE VERDICT READS THE FIELD, it does not restate an assumption made before the evidence
    was reconciled.

    This was the string "guidance unavailable, so the forward verdict cannot be formed",
    written unconditionally — which put the exact contradiction this round exists to remove
    back into the same report, one field lower: a verdict denying the guidance printed directly
    above it.
    """
    g = fields.get("guidance_current")
    if g is not None and g.state is FieldState.OK:
        return ("the company issued guidance with these results (shown above). Whether it is a "
                "RAISE is NOT established: that needs the prior guidance for the same period on "
                "the same accounting basis, which is not stored.")
    return "no guidance is joined to this report, so the forward verdict cannot be formed"


def result_verdict(fields: dict) -> str:
    """Same rule for the accounting basis: say what the report actually holds."""
    b = fields.get("accounting_basis")
    if b is not None and b.state is FieldState.OK:
        return ("see the surprise table. The issuer states a basis for each reported figure; "
                "the ESTIMATE's basis is still unknown, so a surprise against it is a factual "
                "difference and not a verified beat.")
    return "see the surprise table; basis is unverified"


def _pct(field) -> str | None:
    v = getattr(field, "value", None)
    if not isinstance(v, dict):
        return None
    p = v.get("pct")
    return f"{p:+.2f}%" if isinstance(p, (int, float)) else None


def _missing(fields: dict, keys) -> list[str]:
    """Which of the named inputs are not resolved. Named, never counted —
    "8 unavailable" tells a reader nothing about whether the missing ones matter."""
    out = []
    for k in keys:
        f = fields.get(k)
        if f is None or f.state is not FieldState.OK:
            out.append(k.replace("_", " "))
    return out


def post_earnings_assessment(fields: dict) -> dict:
    """The opening read of a post-earnings report, before any of the detail.

    DETERMINISTIC AND DERIVED. Every clause is read off a field's state, so the summary cannot
    drift from the body it summarises — which is the failure mode a narrator would introduce
    first. It says what IS available, what is NOT established, and what the price number does
    and does not mean, in that order.
    """
    have_figures = (fields.get("official_figures") is not None
                    and fields["official_figures"].state is FieldState.OK)
    have_guidance = (fields.get("guidance_current") is not None
                     and fields["guidance_current"].state is FieldState.OK)
    frozen = fields.get("pre_report_link")
    had_baseline = frozen is not None and frozen.state is FieldState.OK

    results = ("Official results are available from the issuer's own release."
               if have_figures else
               "No official issuer release is joined to this report; figures below come from "
               "the provider's earnings row.")
    guidance = (" Current guidance is available." if have_guidance else
                " No company guidance is joined to this report.")
    comparison = (
        " Comparison with the expectations frozen before the release is recorded."
        if had_baseline else
        " Comparison with pre-release expectations remains UNVERIFIED: no frozen baseline "
        "exists for this event, and the stored estimate carries neither units nor an "
        "accounting basis.")
    r1 = fields.get("return_1d")
    window = (r1.value or {}).get("window") if r1 is not None and r1.state is FieldState.OK else None
    price = (
        f" The displayed share-price return ({_pct(r1)}) spans {window} close-to-close; it does "
        f"NOT isolate the announcement's effect."
        if window else
        " No matured share-price reaction is on file for this release.")
    return {
        "read_this_first": results + guidance + comparison + price,
        "available": [k for k in ("official_figures", "guidance_current", "revenue_actual",
                                  "eps_actual", "fiscal_period")
                      if fields.get(k) is not None and fields[k].state is FieldState.OK],
        "not_established": _missing(fields, (
            "pre_report_link", "guidance_change", "revenue_surprise_pct",
            "management_commentary")),
    }


def outlook_assessment(fields: dict, *, subject: str) -> dict:
    """The opening read of a market or stock outlook.

    WHAT GOES FIRST IS WHAT A READER CAME FOR: the latest price, the observed structure, how
    broad it is, and which inputs are missing. Execution status and methodology are real and
    belong in the report; leading with them made the first screen about the disclaimer.
    """
    price = fields.get("price_as_of")
    close = (price.value or {}).get("close") if price is not None and price.state is FieldState.OK else None
    ts = (price.value or {}).get("ts") if price is not None and price.state is FieldState.OK else None
    trend = fields.get("trend_structure")
    structure = (trend.value or {}).get("structure") if trend is not None and trend.state is FieldState.OK else None
    breadth = fields.get("breadth")
    part = (breadth.value or {}).get("participation_pct") if breadth is not None and breadth.state is FieldState.OK else None

    bits = []
    if close is not None:
        bits.append(f"{subject} last closed at {close}" + (f" ({ts})." if ts else "."))
    else:
        bits.append(f"No current close is on file for {subject}.")
    if structure:
        bits.append(f"Observed daily structure: {structure}.")
    if part is not None:
        bits.append(f"Participation: {part}% of covered symbols are above their own 20-bar "
                    f"average.")
    missing = _missing(fields, ("volatility", "macro", "liquidity", "positioning",
                                "rates_credit_fx", "company_condition", "valuation", "news"))
    if missing:
        bits.append("Not joined to this report: " + ", ".join(missing) + ".")
    return {
        "read_this_first": " ".join(bits),
        "scope": "observed condition only; the horizon outlooks below are reported unavailable "
                 "rather than inferred from this one reading",
    }
