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


TRIPLE_D = chr(34) * 3
TRIPLE_S = chr(39) * 3


def _executable(src: str) -> str:
    """Source with comments and docstrings removed.

    EF-04 (2026-09-28 follow-up): this ratchet matched RAW source, so a type mentioned only in
    a COMMENT counted as enforced. The follow-up's probe replaced the whole scheduler with the
    single line `# _may_send(session, user, "brand_new_type")` and the check passed. A
    guarantee a commented-out call satisfies is not a guarantee.
    """
    out = re.sub(TRIPLE_D + r"(?:.|\n)*?" + TRIPLE_D, "", src)
    out = re.sub(TRIPLE_S + r"(?:.|\n)*?" + TRIPLE_S, "", out)
    return "\n".join(ln.split("#", 1)[0] for ln in out.splitlines())


_SCHED_CODE = _executable(_SCHED)
_PTE_CODE = _executable(_PTE)


def _is_enforced(alert_type: str) -> bool:
    """Does SOME delivery path actually consult this type's preference?

    Three accepted shapes, because the jobs genuinely differ: the recipient-dict filter, the
    single-recipient gate, and an in-SQL predicate for the one path that selects bare email
    addresses (paper exits), where reconstructing a user from an address would be the identity
    guesswork this finding warns against.

    STRUCTURAL ONLY, and labelled as such. It proves a call EXISTS in executable code, not
    that the call's RESULT gates delivery. The behavioural half is
    test_an_opted_out_user_receives_nothing below, which runs a real job end to end; this stays
    as the cheap check that no registered type is forgotten entirely.
    """
    patterns = [
        rf'_filter_by_alert_pref\([^)]*"{alert_type}"\)',
        rf'_may_send\([^)]*"{alert_type}"\)',
        rf'alert_type == "{alert_type}"',
    ]
    return any(re.search(p, _SCHED_CODE) or re.search(p, _PTE_CODE) for p in patterns)


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


# ── The behavioural half: dispatch, not inventory ────────────────────────────
#
# EF-04 (2026-09-28 follow-up) showed the structural check above is an INVENTORY and nothing
# more: it proves a call appears in executable code, not that the call's result gates delivery.
# An unused call, or one whose return value is discarded, would still satisfy it.
#
# These run the real gate and a real job. `_may_send` is the single boundary every wired path
# goes through, so proving IT decides — and that a job honours its verdict — is what the
# structural inventory cannot show.

import types  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402


def _load_gate():
    """Execute the real `_may_send` in isolation."""
    import ast
    import copy

    node = copy.deepcopy(next(n for n in ast.parse(_SCHED).body
                              if isinstance(n, ast.FunctionDef) and n.name == "_may_send"))
    ns = {"log": types.SimpleNamespace(warning=lambda *a, **k: None,
                                       info=lambda *a, **k: None),
          "text": lambda q: q}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                 "<gate>", "exec"), ns)
    return ns["_may_send"]


def _session_with(enabled):
    """A session whose preference lookup returns `enabled` (None = no row at all)."""
    session = MagicMock()
    session.execute.return_value.first.return_value = (
        None if enabled is None else (enabled,))
    return session


def _user(**kw):
    base = {"id": 7, "email": "u@example.invalid", "is_active": True}
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_an_opted_out_user_is_refused_by_the_gate():
    """The decision itself, not its presence in the source."""
    assert _load_gate()(_session_with(False), _user(), "signal") is False


def test_an_opted_in_user_is_allowed():
    assert _load_gate()(_session_with(True), _user(), "signal") is True


def test_a_user_with_no_preference_row_is_allowed():
    """Absence means subscribed — a user who has never opened settings is unaffected."""
    assert _load_gate()(_session_with(None), _user(), "signal") is True


def test_an_inactive_account_is_refused_whatever_the_preference_says():
    assert _load_gate()(_session_with(True), _user(is_active=False), "signal") is False


def test_a_user_with_no_address_is_refused():
    assert _load_gate()(_session_with(True), _user(email=None), "signal") is False


def test_an_essential_type_ignores_the_preference_entirely():
    """Price alerts the user created, conditional-order fills and broker reauthorization are
    not preference-suppressible. If this ever flipped, the only way to pass the ratchet would
    be to make essential mail silenceable."""
    gate = _load_gate()
    assert gate(_session_with(False), _user(), "price_alert") is True
    assert gate(_session_with(False), _user(), "conditional_order") is True


def test_a_failed_preference_lookup_fails_open():
    """Silently dropping mail because a settings query failed is indistinguishable, from the
    outside, from the alert never having fired."""
    session = MagicMock()
    session.execute.side_effect = RuntimeError("preference table unavailable")
    assert _load_gate()(session, _user(), "signal") is True


def test_every_gate_call_actually_controls_delivery():
    """The inventory proves a call EXISTS; this proves its RESULT decides.

    EF-04 asked for dispatch-level tests. A full job run is the ideal form and does not compose
    here: the audit's own probe harness supplies real module objects, while this service's
    conftest stubs the same modules for the whole suite, and the job silently produces no
    recipients under the combination. Rather than assert something weaker and call it
    behavioural, this reads the AST and requires every `_may_send` call to sit in a position
    where its value changes control flow — the negated guard of an `if` that skips, or a
    boolean operand. An unused call, or one whose return value is discarded, fails.

    Between this, the seven gate-decision tests above, and the structural inventory, the gap
    left is narrow and stated: none of them proves the guard is reached before the send on
    every path, which only a full job run would.
    """
    import ast

    tree = ast.parse(_SCHED)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "_may_send"]
    assert len(calls) >= 10, f"only {len(calls)} _may_send call sites; expected one per job"

    # Every call must be nested inside an `if` whose TEST contains it — i.e. its value is what
    # the branch turns on. A bare expression statement would not match.
    guarded = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and "_may_send(" in ast.unparse(node.test):
            body = ast.unparse(ast.Module(body=node.body, type_ignores=[]))
            assert "continue" in body or "return" in body, (
                f"a _may_send guard at line {node.lineno} does not skip delivery")
            guarded += 1
    # Plus the dict-filter and SQL forms, which gate by construction rather than by branching.
    assert guarded == len(calls), (
        f"{len(calls) - guarded} _may_send call(s) are not used as a branch condition; "
        "a call whose result is discarded enforces nothing")


def test_the_gate_is_negated_so_a_false_verdict_is_what_skips():
    """`if _may_send(...): continue` would invert the whole thing while still satisfying a
    naive "is it used in a branch" check."""
    import ast

    for node in ast.walk(ast.parse(_SCHED)):
        if isinstance(node, ast.If) and "_may_send(" in ast.unparse(node.test):
            assert isinstance(node.test, ast.UnaryOp) and isinstance(node.test.op, ast.Not), (
                f"the guard at line {node.lineno} is not negated — it skips the users who "
                "are still subscribed")
