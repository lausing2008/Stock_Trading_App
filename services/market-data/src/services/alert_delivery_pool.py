"""AQ06 — a candidate is not a delivered alert, and only one of them is "alerts you received".

THE DEFECT. `_record_options_flow_alert_outcome` writes a row for EVERY candidate, before the
per-recipient loop applies cooldown, ranking and the email cap. The loop already knows what
happened — it computes `deferred`, `queued`, `omitted_by_cap` and `accepted` and logs them —
but nothing persisted, so `options_flow_alert_outcomes` is a table of CANDIDATES wearing the
name of alerts. Every win rate computed over it answers "how did everything we considered
behave", while the reader hears "how did the alerts I got behave".

Both are worth measuring. They are different populations and need separate pools, which is why
this records a state per candidate rather than deleting the ones that were not sent: the full
candidate set is the baseline a delivered subset has to beat to be worth sending.

PROVIDER ACCEPTANCE IS NOT INBOX DELIVERY. `sender_accepted` means the mail provider returned
success. Nothing here establishes that anybody received or opened anything, and the name says
so rather than leaving a reader to assume.
"""
from __future__ import annotations

#: Ordered weakest-to-strongest. Where one candidate reaches different states for different
#: recipients, the STRONGEST wins: the question each state answers is "did this reach anyone".
CANDIDATE = "candidate"
SUPPRESSED_BY_COOLDOWN = "suppressed_by_cooldown"
OMITTED_BY_CAP = "omitted_by_cap"
QUEUED_NOT_SENT = "queued_not_sent"
SENDER_ACCEPTED = "sender_accepted"

ORDER = (CANDIDATE, SUPPRESSED_BY_COOLDOWN, OMITTED_BY_CAP, QUEUED_NOT_SENT, SENDER_ACCEPTED)
_RANK = {s: i for i, s in enumerate(ORDER)}

STATE_MEANING = {
    CANDIDATE: "discovered and recorded; no recipient reached this contract",
    SUPPRESSED_BY_COOLDOWN: "held back by a per-recipient cooldown on its symbol and direction",
    OMITTED_BY_CAP: "ranked out of the payload by the per-email cap",
    QUEUED_NOT_SENT: "ranked into a payload whose send failed or was never attempted",
    SENDER_ACCEPTED: "carried in a payload the mail provider accepted. NOT proof of inbox "
                     "delivery, which this system cannot observe",
}

#: The only pool that answers "the alerts I received", as closely as this system can see.
DELIVERED_POOL = (SENDER_ACCEPTED,)


def merge(current: str | None, candidate_state: str) -> str:
    """Strongest state wins across recipients."""
    if current is None:
        return candidate_state
    return current if _RANK[current] >= _RANK[candidate_state] else candidate_state


def fold_recipient(states: dict, *, deferred, queued, omitted, accepted,
                   send_attempted: bool, send_ok: bool) -> dict:
    """Fold ONE recipient's outcome into the per-chain state map.

    `queued` are the chains that cleared cooldown; `omitted` were ranked out of the payload;
    `accepted` were carried in a payload the sender accepted. A chain that was queued but whose
    send failed is QUEUED_NOT_SENT — distinct from omitted, because nothing ranked it out.
    """
    for chain in deferred:
        states[chain] = merge(states.get(chain), SUPPRESSED_BY_COOLDOWN)
    for chain in omitted:
        states[chain] = merge(states.get(chain), OMITTED_BY_CAP)
    for chain in queued:
        if chain in accepted and send_attempted and send_ok:
            states[chain] = merge(states.get(chain), SENDER_ACCEPTED)
        elif chain not in omitted:
            states[chain] = merge(states.get(chain), QUEUED_NOT_SENT)
    return states


def summarise(all_chains, states: dict) -> dict:
    """Counts per state over the FULL candidate set, including those nobody reached."""
    counts = {s: 0 for s in ORDER}
    per_chain = {}
    for chain in all_chains:
        state = states.get(chain) or CANDIDATE
        per_chain[chain] = state
        counts[state] += 1
    delivered = sum(counts[s] for s in DELIVERED_POOL)
    return {
        "per_chain": per_chain,
        "counts": counts,
        "candidate_total": len(all_chains),
        "delivered_total": delivered,
        "state_meaning": STATE_MEANING,
        "note": ("The candidate set is the baseline a delivered subset must beat to be worth "
                 "sending. Provider acceptance is not inbox delivery."),
    }
