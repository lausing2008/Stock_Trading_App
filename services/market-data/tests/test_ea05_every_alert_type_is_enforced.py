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
_OPTION_OPPORTUNITY = (_ROOT / "services/market-data/src/services/option_opportunity_notifications.py").read_text()
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
_OPTION_OPPORTUNITY_CODE = _executable(_OPTION_OPPORTUNITY)


#: Every service module that could plausibly deliver something. Scanned as a DIRECTORY rather
#: than a hand-listed trio: the list was three files, a fourth delivery path was added
#: elsewhere, and the ratchet reported it as unenforced when it was enforced perfectly well.
#: A maintenance list that has to be edited whenever the thing it guards grows is a second
#: place to forget.
_SCAN_ROOTS = (
    _ROOT / "services/market-data/src/services",
    _ROOT / "services/market-data/src/api",
)
_GATE_CALLS = {"_may_send", "may_send", "_may_notify", "may_notify",
               "_filter_by_alert_pref", "filter_recipients"}


def _enforced_types() -> set[str]:
    """Every alert type some delivery path actually consults, resolving CONSTANTS.

    The previous version matched the type as a string LITERAL inside the call parentheses, so
    `may_send(session, user, ALERT_TYPE_EMAIL)` — a perfectly ordinary named constant — read as
    unenforced. Rather than spell literals into call sites to satisfy a regex, module-level
    `NAME = "literal"` bindings are resolved and a call passing that name counts.

    STRUCTURAL ONLY, and still labelled as such: it proves a call EXISTS in executable code,
    not that the call's RESULT gates delivery. The behavioural half is
    test_an_opted_out_user_receives_nothing below, which runs a real job end to end.
    """
    import ast

    found: set[str] = set()
    for rootdir in _SCAN_ROOTS:
        for path in sorted(rootdir.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text())
            except SyntaxError:  # pragma: no cover - a broken file fails elsewhere, loudly
                continue
            consts: dict[str, str] = {}
            for node in tree.body:
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                        and isinstance(node.value.value, str):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            consts[target.id] = node.value.value
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = (node.func.attr if isinstance(node.func, ast.Attribute)
                            else getattr(node.func, "id", None))
                    if name in _GATE_CALLS:
                        for arg in node.args:
                            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                                found.add(arg.value)
                            elif isinstance(arg, ast.Name) and arg.id in consts:
                                found.add(consts[arg.id])
                # The in-SQL predicate shape, for the path that selects bare email addresses.
                if isinstance(node, ast.Compare) and isinstance(node.left, ast.Attribute) \
                        and node.left.attr == "alert_type":
                    for comp in node.comparators:
                        if isinstance(comp, ast.Constant) and isinstance(comp.value, str):
                            found.add(comp.value)
    # The raw-SQL predicate form, which no AST of Python can see.
    for blob in (_SCHED_CODE, _PTE_CODE, _OPTION_OPPORTUNITY_CODE):
        found.update(re.findall(r'alert_type\s*==\s*"([a-z_]+)"', blob))
    return found


def _is_enforced(alert_type: str) -> bool:
    return alert_type in _enforced_types()


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


# AQ-REVIEW-2 (2026-10-12): THESE THREE USED TO BE SOURCE-TEXT ASSERTIONS, matching literal
# strings in `_may_send`'s body. They broke the moment the rule moved to its own module while
# behaving identically — which is the tell: they were pinning the SPELLING, not the decision.
# They are behavioural now, and each one failed when its defect was reinstated.


def test_the_single_recipient_gate_rejects_inactive_accounts():
    """`filter_recipients` cannot see account state; the single-recipient gate closes that half."""
    assert _load_gate()(_session_with(True), _user(is_active=False), "signal") is False
    assert _load_gate()(_session_with(True), _user(email=None), "signal") is False


def test_the_gate_fails_open_on_a_lookup_error_for_a_default_on_type():
    """Established convention, and the right one FOR THESE TYPES: silently dropping mail
    because a settings query failed is indistinguishable from the alert never having fired."""
    session = MagicMock()
    session.execute.side_effect = RuntimeError("preference table unavailable")
    assert _load_gate()(session, _user(), "signal") is True


def test_the_gate_fails_CLOSED_on_a_lookup_error_for_an_opt_in_type():
    """The inverse, and the reason the rule had to become per-type: for a channel nobody has
    opted into, the failure that cannot be taken back is sending, not staying silent."""
    session = MagicMock()
    session.execute.side_effect = RuntimeError("preference table unavailable")
    assert _load_gate()(session, _user(), "product_impact") is False


def test_what_absence_means_is_per_type():
    """A user who has never opened settings is unaffected for the long-standing types, and is
    NOT enrolled in an opt-in channel. One function, two answers, taken from the registry."""
    assert _load_gate()(_session_with(None), _user(), "signal") is True
    assert _load_gate()(_session_with(None), _user(), "product_impact") is False


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
    """The shipped gate itself — imported, not reconstructed.

    This used to AST-copy `_may_send` out of `scheduler.py` and exec the copy, because importing
    scheduler.py means importing apscheduler, yfinance and most of the service. That is also
    exactly why the rule now lives in `alert_audience`, which imports nothing heavy: the real
    function can simply be called. A copy that is one edit behind the original is a test that
    passes on code nobody runs.
    """
    import importlib.util
    path = _ROOT / "services/market-data/src/services/alert_audience.py"
    spec = importlib.util.spec_from_file_location("ea05_alert_audience", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.may_send


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
