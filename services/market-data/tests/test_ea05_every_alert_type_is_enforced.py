"""EA-05 (2026-09-28 email audit): twelve advertised preference types were never enforced.

THE DEFECT. `ALERT_TYPES` advertised 23 manageable types and the settings page wrote a row for
each. Only eleven were ever consulted by a sending job. For the other twelve — signal,
morning_digest, premarket_brief, squeeze_watch_revert, sr_watch, value_area, earnings_reminder,
portfolio_digest, post_open_digest, theme_forecast, trade_coach, trade_exit — the toggle wrote
a row nothing read and the unsubscribe footer linked to a preference no job consulted. An
unsubscribe control that does not unsubscribe is worse than not offering one.

The flow digest was worse still: it selected EVERY user with a nonempty address, with no
active-account predicate and no registry entry at all, so a disabled account received a send
attempt and there was no way to opt out.

WHY THIS FILE IS A RATCHET, NOT A LIST. The enforcement helper already existed and worked; it
was simply never called from those twelve jobs. That is the failure mode of a per-job check —
every new job has to remember — so the durable fix is not "add twelve calls", it is this test.
Adding a 24th type without wiring its delivery path now fails the build.

Production had one active emailed account and no preference rows, so ordinary usage would never
have surfaced this. That is an explanation of why it hid, not evidence that nobody was affected.
"""
import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SCHED = (_ROOT / "services/market-data/src/services/scheduler.py").read_text()
_PTE = (_ROOT / "services/market-data/src/services/paper_trading_engine.py").read_text()
_PREFS = (_ROOT / "shared/common/alert_prefs.py").read_text()


def _registered_types() -> list[str]:
    """Every key in ALERT_TYPES, read from the real registry."""
    block = _PREFS[_PREFS.index("ALERT_TYPES: list[dict] = ["):]
    block = block[:block.index("\n]")]
    return re.findall(r'\{"key":\s*"([a-z_]+)"', block)


def _essential_types() -> set[str]:
    block = _PREFS[_PREFS.index("ESSENTIAL"):]
    block = block[:block.index("})")]
    return set(re.findall(r'"([a-z_]+)"', block))


def _is_enforced(alert_type: str) -> bool:
    """Does SOME delivery path actually consult this type's preference?

    Three accepted shapes, because the jobs genuinely differ: the recipient-dict filter, the
    single-recipient gate, and an in-SQL predicate for the one path that selects bare email
    addresses (paper exits), where reconstructing a user from an address would be the identity
    guesswork this finding warns against.
    """
    patterns = [
        rf'_filter_by_alert_pref\([^)]*"{alert_type}"\)',
        rf'_may_send\([^)]*"{alert_type}"\)',
        rf'alert_type == "{alert_type}"',
    ]
    return any(re.search(p, _SCHED) or re.search(p, _PTE) for p in patterns)


def test_every_manageable_alert_type_is_enforced():
    """THE RATCHET. A type the settings page offers must be honoured by its sending job."""
    manageable = [t for t in _registered_types() if t not in _essential_types()]
    assert len(manageable) >= 20, f"registry looks truncated: {manageable}"
    unenforced = [t for t in manageable if not _is_enforced(t)]
    assert not unenforced, (
        f"{len(unenforced)} advertised alert type(s) have no preference enforcement in any "
        f"delivery path: {unenforced}. A settings toggle that writes a row nothing reads is "
        f"not a preference. Add _may_send()/_filter_by_alert_pref() to the sending job."
    )


def test_the_twelve_that_were_missing_are_specifically_covered():
    """Named explicitly, so a refactor that drops one is a legible failure rather than a
    change in a count."""
    for t in ("signal", "morning_digest", "premarket_brief", "squeeze_watch_revert",
              "sr_watch", "value_area", "earnings_reminder", "portfolio_digest",
              "post_open_digest", "theme_forecast", "trade_coach", "trade_exit"):
        assert _is_enforced(t), f"{t} lost its enforcement again"


def test_essential_types_are_deliberately_not_required_to_be_filtered():
    """Price alerts the user created, conditional-order fills, broker reauthorization and
    operator notices are not preference-suppressible by design. The ratchet must exempt them,
    or the only way to pass would be to make essential mail silenceable."""
    essential = _essential_types()
    assert {"price_alert", "conditional_order", "broker_reauth"} <= essential


def test_the_flow_digest_is_registered_and_filtered():
    """It had neither a registry entry nor a check — there was no way to turn it off."""
    assert "flow_digest" in _registered_types()
    assert _is_enforced("flow_digest")


def test_the_flow_digest_excludes_inactive_accounts():
    """It selected every user with a nonempty address. An inactive account is not a preference
    question — it is not a recipient."""
    block = _SCHED[_SCHED.index("def send_flow_digest("):]
    block = block[:block.index("\ndef ")]
    assert "User.is_active.is_(True)" in block


def test_the_single_recipient_gate_rejects_inactive_accounts():
    """_filter_by_alert_pref cannot see account state; _may_send is what closes that half."""
    body = _SCHED[_SCHED.index("def _may_send("):]
    body = body[:body.index("\ndef ")]
    code = "\n".join(ln.split("#", 1)[0] for ln in body.splitlines())
    assert 'getattr(user, "is_active", True)' in code
    assert 'getattr(user, "email", None)' in code


def test_the_gate_fails_open_on_a_lookup_error():
    """Established convention, and the right one: silently dropping mail because a settings
    query failed is indistinguishable from the alert never having fired."""
    body = _SCHED[_SCHED.index("def _may_send("):]
    body = body[:body.index("\ndef ")]
    tail = body[body.index("except Exception"):]
    assert "return True" in tail[:400]


def test_absence_of_a_row_means_subscribed():
    """A user who has never opened settings must be unaffected — only an explicit disable
    removes anyone."""
    body = _SCHED[_SCHED.index("def _may_send("):]
    body = body[:body.index("\ndef ")]
    assert "return True if row is None else bool(row[0])" in body
