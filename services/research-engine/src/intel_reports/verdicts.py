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
