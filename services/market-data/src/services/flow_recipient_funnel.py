"""Per-user options-flow dispositions; sender acceptance is not inbox delivery."""
from datetime import date, datetime, timezone
from sqlalchemy import select
from db import OptionsFlowRecipientDecision


def record(session, *, user_id: int, candidates: dict, fired_date: date,
           accepted: set[str], queued: list[str], omitted: list[str],
           deferred: list[str], previously_seen: set[str], send_attempted: bool,
           send_ok: bool) -> None:
    existing = {r.option_chain: r for r in session.execute(
        select(OptionsFlowRecipientDecision).where(
            OptionsFlowRecipientDecision.user_id == user_id,
            OptionsFlowRecipientDecision.fired_date == fired_date,
            OptionsFlowRecipientDecision.option_chain.in_(list(candidates)),
        )).scalars()}
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for chain, candidate in candidates.items():
        if chain in accepted:
            state, reason = "sender_accepted", None
        elif chain in queued and send_attempted and not send_ok:
            state, reason = "sender_failed", "mail provider did not accept the payload"
        elif chain in omitted:
            state, reason = "omitted_by_cap", "ranked below email limit"
        elif chain in deferred:
            state, reason = "suppressed_by_cooldown", "pair cooldown or larger contract chosen"
        elif chain in previously_seen:
            state, reason = "previously_accepted", "already in this user's seen set"
        else:
            state, reason = "candidate", "not selected for email"
        row = existing.get(chain)
        if row is None:
            session.add(OptionsFlowRecipientDecision(user_id=user_id, option_chain=chain,
                symbol=candidate["symbol"], fired_date=fired_date,
                state=state, reason=reason, decided_at=now))
        elif row.state != "sender_accepted":
            row.state, row.reason, row.decided_at = state, reason, now
