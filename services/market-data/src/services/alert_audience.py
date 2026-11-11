"""Who may be sent an alert type, and over which channel. One implementation, no heavy imports.

AQ-REVIEW-2/3 (2026-10-12). This logic lived inside `scheduler.py`, a module that imports
apscheduler, yfinance and most of the service. Two consequences, both of which showed up as real
defects rather than as tidiness complaints:

  * It could not be tested against a real database without standing the whole scheduler up, so
    every test injected a `may_send` lambda instead. Those lambdas did not implement the same
    rule as the shipped gate — one of them omitted the email check entirely — so the suite was
    green on behaviour the live caller could not produce.
  * The rule drifted from the registry. `alert_prefs` grew a `default_enabled` field and the
    settings endpoint honoured it; this gate never read it and kept returning True for a missing
    row, so an account could see a channel switched off in settings while the sender admitted it.

AUDIENCE AND CHANNEL ARE SEPARATE QUESTIONS and are separate functions here. "This person wants
this kind of notice" and "we have an email address to write to" are different facts; fusing them
meant an opted-in user with no email address was recorded as having the preference disabled,
which is not what happened, and never got the in-app notice that was supposed to be their
fallback.
"""
from __future__ import annotations

from sqlalchemy import text

try:  # pragma: no cover - logging is optional here, the rule is not
    from common.logging import get_logger
    log = get_logger(__name__)
except Exception:  # noqa: BLE001
    class _Null:
        def warning(self, *a, **k):
            pass
    log = _Null()


def _stored_preference(session, user_id: int, alert_type: str):
    """(stored_value_or_None, lookup_failed). The caller decides what either one means."""
    try:
        row = session.execute(
            text("SELECT enabled FROM alert_preferences "
                 "WHERE user_id = :u AND alert_type = :at"),
            {"u": user_id, "at": alert_type},
        ).first()
    except Exception as exc:  # noqa: BLE001
        log.warning("alert_prefs.single_lookup_failed", alert_type=alert_type,
                    user_id=user_id, error=str(exc))
        return None, True
    return (None if row is None else bool(row[0])), False


def may_notify(session, user, alert_type: str) -> bool:
    """Is this account an eligible AUDIENCE for `alert_type`, channel set aside?"""
    from common.alert_prefs import effective_enabled

    if user is None:
        return False
    # An inactive account is not a preference question — it is not a recipient.
    if not getattr(user, "is_active", True):
        return False
    uid = getattr(user, "id", None)
    if uid is None:
        return effective_enabled(alert_type, None)
    stored, failed = _stored_preference(session, uid, alert_type)
    return effective_enabled(alert_type, stored, lookup_failed=failed)


def may_send(session, user, alert_type: str) -> bool:
    """May we EMAIL this account about `alert_type`? Audience, then channel."""
    if user is None or not getattr(user, "email", None):
        return False
    return may_notify(session, user, alert_type)


def filter_recipients(session, recipients: dict, alert_type: str) -> dict:
    """Apply the type's audience rule to an already-built recipient dict.

    An OPT-IN type inverts the query. Asking for `enabled = FALSE` rows and removing them cannot
    express "keep only the accounts that said yes" — in that shape absence is indistinguishable
    from consent, which is the defect. So the declared default picks the question.
    """
    from common.alert_prefs import opt_in_only

    # AUD-SQZ-INACTIVE-RECIPIENTS (2026-11-10): the batch alerts build recipients from open
    # PriceAlert rows and call only this filter, so a deactivated account with a leftover alert
    # kept receiving every squeeze, flow and dark-pool email. `may_notify` already refuses an
    # inactive account; the batch path now applies the same rule. An unknown state (no
    # attribute) is kept, matching may_notify's own default.
    recipients = {uid: u for uid, u in (recipients or {}).items()
                  if getattr(u, "is_active", True) is not False}
    if not recipients:
        return recipients
    if opt_in_only(alert_type):
        try:
            opted_in = {
                r[0] for r in session.execute(
                    text("SELECT user_id FROM alert_preferences "
                         "WHERE alert_type = :at AND enabled = TRUE"),
                    {"at": alert_type},
                ).all()
            }
        except Exception as exc:  # noqa: BLE001
            # FAILS CLOSED, unlike the branch below, and for the reason the two branches exist:
            # here the recoverable mistake is a message that did not arrive.
            log.warning("alert_prefs.lookup_failed_opt_in", alert_type=alert_type, error=str(exc))
            return {}
        return {uid: u for uid, u in recipients.items() if uid in opted_in}
    try:
        opted_out = {
            r[0] for r in session.execute(
                text("SELECT user_id FROM alert_preferences "
                     "WHERE alert_type = :at AND enabled = FALSE"),
                {"at": alert_type},
            ).all()
        }
    except Exception as exc:  # noqa: BLE001
        # FAILS OPEN: silently dropping alerts because a settings query failed is
        # indistinguishable, from the outside, from the alert never having fired.
        log.warning("alert_prefs.lookup_failed", alert_type=alert_type, error=str(exc))
        return recipients
    if not opted_out:
        return recipients
    return {uid: u for uid, u in recipients.items() if uid not in opted_out}


def owned_symbols_by_user(session, user_ids) -> dict:
    """{user_id: {SYMBOL, ...}}: the symbols each account follows itself.

    AUD-DP-AUDIENCE (2026-11-10). "Own" means the account's watchlist items plus its open
    (untriggered) price alerts. Watchlist ownership is resolved both ways, as in the October 24
    owned-watchlist fix: an item's direct `user_id`, or the owner of the list it sits in. A lookup
    failure returns {} for everyone, so a caller that filters on this sends NOTHING rather than
    the whole shared list: "only your own symbols" cannot fail open into "everyone's".
    """
    from db import PriceAlert, Stock, Watchlist, WatchlistItem
    from sqlalchemy import select

    wanted = {uid for uid in (user_ids or []) if uid is not None}
    if not wanted:
        return {}
    out: dict = {}
    try:
        for direct, owner, symbol in session.execute(
                select(WatchlistItem.user_id, Watchlist.user_id, Stock.symbol)
                .join(Stock, Stock.id == WatchlistItem.stock_id)
                .outerjoin(Watchlist, Watchlist.id == WatchlistItem.watchlist_id)):
            uid = direct or owner
            if uid in wanted and symbol:
                out.setdefault(uid, set()).add(symbol.upper())
        for uid, symbol in session.execute(
                select(PriceAlert.user_id, PriceAlert.symbol).where(PriceAlert.triggered.is_(False))):
            if uid in wanted and symbol:
                out.setdefault(uid, set()).add(symbol.upper())
    except Exception as exc:  # noqa: BLE001
        log.warning("alert_audience.owned_symbols_failed", error=str(exc))
        return {}
    return out
