"""The 2026-09-28 email alert audit — EA-01 … EA-12.

EA-01 IS THE ONE THAT MATTERS MOST, and it was mine. `check_signal_alerts` evaluated
`_signal_cohort_stats(session, new_signal, style)` inline as an argument, where `new_signal` is
the KEYWORD NAME of another argument in the same call rather than a local variable. Every
evaluation raised NameError before the sender was entered; the surrounding try counted that as
a delivery failure; and after five of them the give-up branch advanced `last_signal`, consuming
a transition nobody was ever told about.

Introduced by f7e9fea3 on 2026-09-23. Production's most recent signal email is
2026-09-24 03:40:45 against 362 subscriptions — roughly four days of complete silence, with
transitions permanently discarded along the way. Every test in the suite stayed green, because
nothing executed that call.

The rest of the findings are recorded against their own tests below.
"""
import ast
import copy
import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SCHED = (_ROOT / "services/market-data/src/services/scheduler.py").read_text()
_EMAIL = (_ROOT / "services/market-data/src/services/email_service.py").read_text()
_PTE = (_ROOT / "services/market-data/src/services/paper_trading_engine.py").read_text()


def _fn_src(src: str, name: str) -> str:
    start = src.index(f"\ndef {name}(")
    end = src.index("\ndef ", start + 1)
    return src[start:end]


def _code(text: str) -> str:
    return "\n".join(ln.split("#", 1)[0] for ln in text.splitlines())


def _load(src: str, name: str, env: dict | None = None):
    """Execute one real top-level function in isolation."""
    node = copy.deepcopy(next(n for n in ast.parse(src).body
                              if isinstance(n, ast.FunctionDef) and n.name == name))
    node.decorator_list = []
    ns = dict(env or {})
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                 "<audit>", "exec"), ns)
    return ns[name]



def _build_plan(fundamentals: dict, style: str = "SWING", price: float = 100.0):
    """Run the REAL _build_game_plan with a stubbed price feed.

    Behavioural rather than source-text throughout: an earlier draft of these tests asserted on
    expressions like `_dte == 0` and `max(1.03, ...)`, which pins NUMBERS as substrings — the
    exact pattern AUD-T401's ratchet exists to stop, and it failed the build. Running the real
    function against adversarial inputs checks the same properties and survives a rewrite.
    """
    import sys
    from types import ModuleType, SimpleNamespace
    from unittest.mock import patch

    import pandas as pd

    env = {"log": type("_L", (), {"warning": lambda *a, **k: None,
                                  "info": lambda *a, **k: None})()}
    _exec_names(_SCHED, ["_STYLE_PARAMS"], env)
    for name in ("_round_step", "_plan_geometry_ok", "_build_game_plan"):
        env[name] = _load(_SCHED, name, env)
    yf = ModuleType("yfinance")
    yf.Ticker = lambda s: SimpleNamespace(history=lambda **kw: pd.DataFrame({"Close": [price]}))
    with patch.dict(sys.modules, {"yfinance": yf}):
        return env["_build_game_plan"]("TEST", {"reasons": {}}, fundamentals, style=style)


def _exec_names(src: str, names: list[str], env: dict) -> None:
    """Execute named module-level assignments into `env`."""
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id in names for t in targets):
                exec(compile(ast.fix_missing_locations(ast.Module(body=[copy.deepcopy(node)],
                                                                  type_ignores=[])),
                             "<audit>", "exec"), env)


# ── EA-01: the NameError that silenced every AI signal email ─────────────────

def test_the_cohort_badge_no_longer_references_an_undefined_name():
    """THE OUTAGE. `new_signal` is a keyword argument name; referencing it as a value raises."""
    body = _code(_fn_src(_SCHED, "check_signal_alerts"))
    assert "_signal_cohort_stats(session, new_signal, style)" not in body, \
        "the NameError is back — this silences every signal email"
    assert "_signal_cohort_stats(session, current, style)" in body


def test_the_cohort_badge_is_computed_separately_so_it_can_fail_alone():
    """A win-rate BADGE failing must never cost the reader the alert itself. Evaluated inline as
    an argument, any error in it took the whole send down with it."""
    body = _code(_fn_src(_SCHED, "check_signal_alerts"))
    assert "cohort_stats=_cohort_stats," in body
    assert "signal_alert.cohort_stats_failed" in _SCHED
    # The badge is computed BEFORE the send call, in its own try.
    assert body.index("_cohort_stats = _signal_cohort_stats(") < body.index("email_ok = send_signal_alert_email(")


def test_a_code_level_failure_does_not_consume_the_transition():
    """DP-1's give-up exists for a broken SMTP config that will never recover on its own. An
    exception from our own code is the opposite: it succeeds the moment the code is fixed, and
    advancing `last_signal` discards the notification permanently. That is exactly what
    happened here for four days."""
    body = _code(_fn_src(_SCHED, "check_signal_alerts"))
    assert "_send_raised = True" in body
    assert "if _send_raised:" in body
    assert "signal_alert.send_raised_not_consuming" in _SCHED
    # The force-advance must sit on the OTHER branch.
    # Structural, via the AST: the force-advance must live on the `elif` arm, so a raised
    # error can never reach it. Checked as a TREE rather than by matching the retry ceiling as
    # text — that would pin a number as a substring, which is what the T401 ratchet forbids.
    fn = next(n for n in ast.walk(ast.parse(_SCHED))
              if isinstance(n, ast.FunctionDef) and n.name == "check_signal_alerts")
    guard = next(n for n in ast.walk(fn)
                 if isinstance(n, ast.If) and isinstance(n.test, ast.Name)
                 and n.test.id == "_send_raised")
    assert guard.orelse, "the retry ceiling must be an elif, not a sibling statement"
    on_raise = ast.unparse(ast.Module(body=guard.body, type_ignores=[]))
    assert "last_signal" not in on_raise, "a code error still consumes the transition"
    on_else = ast.unparse(ast.Module(body=guard.orelse, type_ignores=[]))
    assert "alert.last_signal = current" in on_else


# ── EA-02: a bullish plan whose take-profit sat below its stop ───────────────

def _geometry():
    return _load(_SCHED, "_plan_geometry_ok")


def test_a_target_below_the_stop_is_rejected():
    """The audit's counterexample, as arithmetic: entries 98.5/96.5, stop 94.5, target 90."""
    ok, why = _geometry()({"entry1": 98.5, "entry2": 96.5, "breakout": 102.0,
                           "stop": 94.5, "take_profit": 90.0, "current_price": 100.0})
    assert ok is False and "take profit" in why


def test_a_coherent_long_plan_passes():
    ok, why = _geometry()({"entry1": 98.5, "entry2": 96.5, "breakout": 102.0,
                           "stop": 94.5, "take_profit": 112.0, "current_price": 100.0})
    assert ok is True, why


def test_every_level_must_be_finite_and_positive():
    """NaN fails every comparison silently, so a NaN level would slip through each `<` test
    without raising. isfinite is what catches it."""
    base = {"entry1": 98.5, "entry2": 96.5, "breakout": 102.0, "stop": 94.5,
            "take_profit": 112.0, "current_price": 100.0}
    for bad in (float("nan"), float("inf"), 0.0, -5.0, None, "x"):
        assert _geometry()({**base, "take_profit": bad})[0] is False, bad


def test_the_analyst_target_floor_is_above_the_current_price():
    """`min(1.03, tp_mult * 0.8)` resolved to 0.896 for SWING — the target only had to exceed
    89.6% OF THE PRICE. `min` was reached for by symmetry with the 1.03 floor and silently
    inverted the test, because 0.8 x any multiplier above 1.0 is still below 1.0 here."""
    # The defect was the CHOICE OF FUNCTION, not the constants — `min` of a 1.03 floor and a
    # sub-1.0 multiplier resolves to the multiplier and inverts the test. Read from the AST so
    # no number is pinned as text.
    fn = next(n for n in ast.walk(ast.parse(_SCHED))
              if isinstance(n, ast.FunctionDef) and n.name == "_build_game_plan")
    floors = [n for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
              and n.func.id in ("min", "max")
              and "min_tp_pct" in ast.unparse(n)]
    assert floors, "the analyst-target floor is gone"
    assert all(f.func.id == "max" for f in floors), (
        "the floor uses min(), which resolves BELOW the current price and accepts a target "
        "under the stop")

    # And behaviourally: a target under the price must never become the take-profit.
    plan = _build_plan({"target_price": 90.0})
    assert plan is None or plan["take_profit"] > plan["stop"]
    assert plan is None or plan["take_profit"] != 90.0


def test_an_invalid_plan_is_not_rendered_at_all():
    """Returning None is an established, handled outcome — the alert still sends, without a
    plan section. An instruction that contradicts itself is worse than no instruction."""
    body = _code(_fn_src(_SCHED, "_build_game_plan"))
    assert "_ok, _why = _plan_geometry_ok(plan)" in body
    assert "game_plan.rejected_invalid_geometry" in _SCHED


# ── EA-03: earnings today, and invented rationale ────────────────────────────

def test_zero_days_to_earnings_is_not_treated_as_missing():
    """`days_to_earnings or 99` turned "earnings are TODAY" into 99 and rendered
    "clean runway" — exactly inverted, on the day event risk is highest."""
    body = _code(_fn_src(_SCHED, "_build_game_plan"))
    assert "days_to_earnings or 99" not in body
    assert 'days_to_earnings or "?"' not in body

    # Behavioural: each state renders as itself.
    today = _build_plan({"next_earnings_date": "2026-09-28", "days_to_earnings": 0})
    assert any("TODAY" in c for c in today["catalysts"]), today["catalysts"]
    assert not any("clean runway" in c for c in today["catalysts"])

    soon = _build_plan({"next_earnings_date": "2026-10-01", "days_to_earnings": 3})
    assert any("3d" in c for c in soon["catalysts"])

    far = _build_plan({"next_earnings_date": "2026-11-07", "days_to_earnings": 40})
    assert any("clean runway" in c for c in far["catalysts"])

    overdue = _build_plan({"next_earnings_date": "2026-09-01", "days_to_earnings": -5})
    assert any("already reported" in c for c in overdue["catalysts"])

    unknown = _build_plan({"next_earnings_date": "2026-10-10"})
    assert any("timing unconfirmed" in c for c in unknown["catalysts"])


def test_no_evidence_does_not_become_bullish_rationale():
    """With no reasons and no fundamentals the helper asserted analyst agreement, improving
    structure and supporting volume — three specific claims about data it never received."""
    # Comments stripped: the replacement's own comment QUOTES the three invented claims while
    # explaining why they were removed. Sixth prose collision of this session — a comment that
    # names the thing under test is the most reliable way to break a source assertion.
    body = _code(_fn_src(_SCHED, "_build_game_plan"))
    assert "AI signal + analyst consensus aligned" not in body
    assert "Technical structure improving" not in body
    assert "No supporting catalyst data available" in body


def test_the_risk_line_also_stopped_skipping_zero():
    # No source assertion on the old expression: writing it out pins a threshold as a
    # substring, which is the AUD-T401 pattern. The behaviour is the claim anyway — the old
    # truthiness test is only interesting because of what it DID on the day of the print.
    # Behavioural: the day of the print must not fall through to the generic market sentence.
    today = _build_plan({"next_earnings_date": "2026-09-28", "days_to_earnings": 0})
    assert "Earnings TODAY" in today["risk"], today["risk"]


# ── EA-04: a bullish trigger rendered as a price fall ────────────────────────

def _render(**kw):
    sent = []
    fn = _load(_EMAIL, "send_price_alert_email",
               {"send_email": lambda *a: sent.append(a) or True})
    fn(**kw)
    return {"subject": sent[0][1], "html": sent[0][2], "text": sent[0][3]}


def test_an_indicator_condition_is_not_described_as_a_price_fall():
    """The template read `condition == "above"` and called everything else "fallen below" — so
    a bullish MACD cross rendered as "Price Alert: TEST has fallen below 0.0"."""
    out = _render(to="a@b.invalid", symbol="TEST", condition="MACD bullish crossover",
                  threshold=0.0, price=100.0, note=None)
    assert "fallen below" not in out["subject"]
    assert "MACD bullish crossover" in out["subject"]
    assert "MACD" in out["html"], "the condition was dropped from the body entirely"


def test_a_real_price_crossing_still_reads_exactly_as_before():
    """The fix must not disturb the case that was always correct."""
    assert "risen above" in _render(to="a@b.invalid", symbol="T", condition="above",
                                    threshold=95.0, price=100.0, note=None)["subject"]
    assert "fallen below" in _render(to="a@b.invalid", symbol="T", condition="below",
                                     threshold=105.0, price=100.0, note=None)["subject"]


def test_a_recurring_alert_is_not_told_it_will_never_fire_again():
    """The footer asserted one-shot behaviour for every alert, including the recurring ones the
    technical scheduler explicitly keeps active."""
    rec = _render(to="a@b.invalid", symbol="T", condition="RSI above 70", threshold=70.0,
                  price=72.0, note=None, recurring=True)
    assert "will not fire again" not in rec["html"]
    one = _render(to="a@b.invalid", symbol="T", condition="above", threshold=95.0,
                  price=100.0, note=None, recurring=False)
    assert "will not fire again" in one["html"]


def test_the_technical_scheduler_passes_its_recurrence_flag():
    body = _code(_fn_src(_SCHED, "check_technical_alerts"))
    assert "recurring=bool(alert.recurring)" in body


# ── EA-06 / EA-07: a notification consumed before it was delivered ───────────

def test_a_failed_price_alert_email_is_retried():
    """`triggered` is committed before the send — correct, the market event happened — but a
    failed send left nothing retryable and the next scan filtered the alert out entirely."""
    body = _code(_fn_src(_SCHED, "check_price_alerts"))
    assert "PriceAlert.last_sent_at.is_(None)" in body
    assert "alert.retrying_undelivered" in _SCHED
    assert "_delivered_ids" in body


def test_delivery_is_recorded_only_after_a_successful_send():
    """If last_sent_at were stamped unconditionally the retry above would never terminate on a
    real failure — it would simply stop retrying."""
    body = _code(_fn_src(_SCHED, "check_price_alerts"))
    frag = body[body.index("for kwargs in pending_emails:"):]
    frag = frag[:frag.index("for url, payload in pending_webhooks:")]
    assert "if ok:" in frag and "_delivered_ids.append(_alert_id)" in frag


def test_the_retry_window_is_bounded():
    """A notification nobody could deliver in a day is stale news; retrying it forever would be
    its own defect."""
    body = _code(_fn_src(_SCHED, "check_price_alerts"))
    assert "PriceAlert.triggered_at >= _retry_cutoff" in body
    # The window's VALUE, read from the AST and checked as a number rather than matched as
    # text — `hours=24` as a substring survives being changed to `hours=24 * 99999`.
    fn = next(n for n in ast.walk(ast.parse(_SCHED))
              if isinstance(n, ast.FunctionDef) and n.name == "check_price_alerts")
    cutoff = next(n for n in ast.walk(fn)
                  if isinstance(n, ast.Assign)
                  and any(getattr(t, "id", "") == "_retry_cutoff" for t in n.targets))
    hours = next(kw.value for c in ast.walk(cutoff)
                 if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "timedelta"
                 for kw in c.keywords if kw.arg == "hours")
    hours = ast.literal_eval(hours) if isinstance(hours, ast.Constant) else eval(
        compile(ast.Expression(hours), "<w>", "eval"), {"__builtins__": {}}, {})
    assert 0 < hours <= 72, f"retry window is {hours}h; a stale notification is not news"


def test_top3_composition_is_not_consumed_by_a_failed_delivery():
    """The global composition was written before any send. If every delivery failed, the next
    scan saw an unchanged composition and never retried anyone."""
    body = _code(_fn_src(_SCHED, "check_top3_conviction"))
    set_at = body.index('_rc.set("stockai:top3_last_composition"')
    loop_at = body.index("for uid, user in recipients.items():")
    assert loop_at < set_at, "the composition must advance AFTER the delivery loop"


def test_partial_delivery_does_not_advance_the_composition_either():
    """Advancing on partial success silences the retry for whoever failed — the same defect,
    one recipient narrower."""
    assert "top3_conviction.composition_not_advanced" in _SCHED
    # The advance must be GUARDED by the failure count, read structurally.
    fn = next(n for n in ast.walk(ast.parse(_SCHED))
              if isinstance(n, ast.FunctionDef) and n.name == "check_top3_conviction")
    # The WRITE specifically — the equality check above also reads this key, and picking the
    # read instead makes the guard look absent.
    advance = next(n for n in ast.walk(fn)
                   if isinstance(n, ast.Call)
                   and getattr(n.func, "attr", "") == "set"
                   and "top3_last_composition" in ast.unparse(n))
    guards = [n for n in ast.walk(fn)
              if isinstance(n, ast.If) and "failed" in ast.unparse(n.test)
              and advance.lineno >= n.lineno and advance.end_lineno <= n.end_lineno]
    assert guards, "the composition advance is not guarded by the failure count"


# ── EA-08: freshness and the safety veto ─────────────────────────────────────

def test_an_unverified_freshness_assumption_blocks_a_new_BUY_only():
    """Failing open is right for an EXIT and wrong for a BUY: withholding an exit costs a
    chance to reduce risk, emitting a BUY on unverified inputs asks for money."""
    body = _code(_fn_src(_SCHED, "check_signal_alerts"))
    assert "assumed_fresh_symbols" in body
    assert "signal_alert.buy_suppressed_unverified_freshness" in _SCHED
    frag = body[body.index("assumed_fresh_symbols"):]
    assert '== "BUY"' in frag[:frag.index("style = getattr")]


def test_an_unavailable_decision_engine_defers_the_buy():
    """A safety veto that fails open is not a veto. A non-200 fell through with no branch at
    all and an exception was swallowed at debug level."""
    body = _code(_fn_src(_SCHED, "check_signal_alerts"))
    assert "_de_available = False" in body
    assert "if not _de_available:" in body
    assert "signal_alert.de_gate_unavailable" in _SCHED


# ── EA-09: a tie classified as aggressive buying ─────────────────────────────

def _classify():
    return _load(_SCHED, "_classify_flow_side",
                 {"_FLOW_SIDE_MIN_IMBALANCE": _load_const("_FLOW_SIDE_MIN_IMBALANCE")})


def _load_const(name: str):
    m = re.search(rf"^{name}\s*=\s*([0-9.]+)", _SCHED, re.M)
    assert m, name
    return float(m.group(1))


def test_equal_premium_is_neutral_not_bullish():
    """`ask >= bid` resolved a dead tie to ask-dominant every time, and the template called
    that "aggressive BUYING"."""
    assert _classify()(100.0, 100.0)[0] == "neutral"


def test_a_marginal_split_is_neutral():
    assert _classify()(55.0, 45.0)[0] == "neutral"


def test_a_real_imbalance_still_classifies():
    assert _classify()(80.0, 20.0)[0] == "ask"
    assert _classify()(20.0, 80.0)[0] == "bid"


def test_no_classified_premium_is_neutral_rather_than_a_division_error():
    assert _classify()(0.0, 0.0) == ("neutral", 0.0)


def test_a_neutral_print_produces_no_directional_candidate():
    body = _code(_fn_src(_SCHED, "check_options_flow_alerts"))
    assert '_side, _imbalance = _classify_flow_side(ask, bid)' in body
    # EF-05 (2026-09-28 follow-up) added "unknown" alongside "neutral": a MISSING side is not a
    # measured zero, and `or 0.0` had been turning one reported number into total dominance.
    assert 'if _side in ("neutral", "unknown"):' in body
    assert "ask_side_dominant = ask >= bid" not in body


def test_the_template_reports_the_measured_imbalance_not_an_intent():
    """The print and its price are observations; which side was the aggressor is an inference,
    and it establishes neither opening-versus-closing nor a directional bet."""
    assert "aggressive BUYING (ask-side)" not in _EMAIL
    assert "ask-side dominant" in _EMAIL
    assert "side_imbalance" in _EMAIL


# ── EA-12: transport honesty ─────────────────────────────────────────────────

def test_smtp_has_an_explicit_timeout():
    """smtplib defaults to the global socket timeout, which is None — a blocked transport hangs
    indefinitely and can outlive the scheduler lock lease around it."""
    assert "timeout=_SMTP_TIMEOUT_S" in _EMAIL
    m = re.search(r"^_SMTP_TIMEOUT_S\s*=\s*(\d+)", _EMAIL, re.M)
    assert m and 0 < int(m.group(1)) <= 120


def test_the_exit_email_log_reflects_the_senders_result():
    """This logged `paper.exit_email_sent` regardless of the boolean, so a provider rejection
    was recorded as a success — a log saying the opposite of what happened."""
    body = _code(_fn_src(_PTE, "_send_exit_emails"))
    assert "_exit_ok = send_trade_exit_email(" in body
    assert "if _exit_ok:" in body
    assert "paper.exit_email_not_delivered" in _PTE


# ── EF-01…EF-05: defects introduced BY the EA remediation ────────────────────
#
# The follow-up review found five. Three were mine, created while fixing EA-01/EA-06/EA-09 —
# which is the reason this section exists rather than being folded into the EA tests above:
# a fix's own regressions deserve their own names.

def test_the_send_error_message_survives_its_handler():
    """EF-01, AND IT ABORTED THE WHOLE BATCH.

    Python DELETES the name bound by `except ... as` when the handler exits. My EA-01 fix read
    `_send_exc` further down, in the `_send_raised` branch, which raised UnboundLocalError
    inside the per-recipient error path — and the outer job handler caught that and stopped the
    loop. One persistently failing subscription starved every recipient after it.

    A plain string survives the handler. The exception object does not."""
    body = _code(_fn_src(_SCHED, "check_signal_alerts"))
    assert "_send_error_msg = str(_send_exc)" in body
    # The later use must be the string, never the exception.
    give_up = body[body.index("if _send_raised:"):]
    give_up = give_up[:give_up.index("session.commit()")]
    assert "str(_send_exc)" not in give_up, "the exception is read outside its handler again"
    assert "error=_send_error_msg" in give_up
    # Initialised before the try, so the name always exists.
    assert body.index('_send_error_msg = ""') < body.index("email_ok = send_signal_alert_email(")


def test_one_failing_recipient_does_not_starve_the_next():
    """EF-01's acceptance. Asserted structurally — the exception never escapes the recipient
    loop, so the loop continues — because the full two-recipient run belongs to the audit's own
    probe harness, which does not compose with this suite's module stubs."""
    import ast

    # Word-boundary, not substring: the flag this very fix introduced is `_send_raised`, which
    # CONTAINS "raise" — so a naive `"raise" not in frag` fails on its own fix.
    fn = next(n for n in ast.walk(ast.parse(_SCHED))
              if isinstance(n, ast.FunctionDef) and n.name == "check_signal_alerts")
    handler = next(h for n in ast.walk(fn) if isinstance(n, ast.Try)
                   for h in n.handlers
                   if h.name == "_send_exc")
    assert not [n for n in ast.walk(handler) if isinstance(n, ast.Raise)], \
        "the per-recipient handler re-raises, which aborts the batch"


def test_a_retry_never_renders_the_threshold_as_the_current_price():
    """EF-02. `prices.get(_ua.symbol, _ua.threshold)` supplied the CONFIGURED THRESHOLD as the
    displayed price — and retry symbols were not in the price fetch, so the default always
    won. An alert set at 90 was emailed as "is now 90.0000" whatever the market was doing.
    That is not a stale quote; no observation supports it."""
    body = _code(_fn_src(_SCHED, "check_price_alerts"))
    assert "prices.get(_ua.symbol, _ua.threshold)" not in body
    assert "_retry_price = prices.get(_ua.symbol)" in body
    assert "if _retry_price is None:" in body
    assert "alert.retry_deferred_no_quote" in _SCHED


def test_retry_symbols_are_included_in_the_price_fetch():
    """The other half of EF-02: without this the quote is never available and every retry
    defers forever."""
    body = _code(_fn_src(_SCHED, "check_price_alerts"))
    assert "{u.symbol for u in _undelivered}" in body


def test_a_delayed_notification_says_that_it_is_delayed():
    """A message arriving hours late, showing a current price, reads as a fresh trigger."""
    body = _fn_src(_SCHED, "check_price_alerts")
    assert "Delayed notification" in body
    assert "_ua.triggered_at.isoformat()" in body


def test_the_retry_only_claims_alerts_this_job_can_render():
    """EF-03. `price_alerts` holds technical conditions too, and check_technical_alerts sets
    `triggered` without stamping `last_sent_at` — so a SUCCESSFULLY delivered one-shot MACD
    alert matched the retry query exactly like a failed price alert, and would have been
    re-sent through the PRICE renderer with the raw enum and the threshold as its price."""
    body = _code(_fn_src(_SCHED, "check_price_alerts"))
    assert "PriceAlert.condition.in_([AlertCondition.ABOVE, AlertCondition.BELOW])" in body


def test_technical_sends_record_delivery_under_the_same_contract():
    """The other half of EF-03: if technical sends never stamp `last_sent_at`, their rows stay
    indistinguishable from undelivered ones for any job that reads that column."""
    body = _code(_fn_src(_SCHED, "check_technical_alerts"))
    assert "_tech_delivered" in body
    assert "update(PriceAlert)" in body
    frag = body[body.index("for kwargs in pending_emails:"):]
    assert "if ok:" in frag and "_tech_delivered.append(_t_alert_id)" in frag


def test_legacy_rows_are_closed_out_rather_than_read_as_pending():
    """EF-03's fifth point. `last_sent_at` only began recording delivery on 2026-09-28, so
    every earlier triggered row has it NULL whether or not its mail went out. Re-sending them
    would mail people price alerts that are weeks old. The audit's own guidance: do not
    interpret every legacy null as a failed delivery.

    MOVED 2026-09-29 by EC-01. This test used to assert the statement's presence in
    `_apply_isolated_ddl()`'s list, which is where it was originally — and wrongly — placed. That
    list runs on EVERY startup of every backend service, and a triggered row with a NULL
    `last_sent_at` is precisely what a FAILED SEND looks like, so the next restart would have
    stamped every genuinely-pending alert as delivered with zero transport calls. The closure
    review caught it. The statement now lives in `_apply_one_shot_migrations()` behind a ledger
    and a deploy watermark.

    This remains a structural check that the closeout still exists at all. Its actual behaviour
    — including the once-only guarantee this test's own earlier version would have let regress —
    is covered against a real database by `test_ec01_one_shot_migration.py`."""
    session_src = (_ROOT / "shared/db/session.py").read_text()
    assert "legacy-price-alert-delivery-closeout" in session_src
    assert "UPDATE price_alerts SET last_sent_at = triggered_at" in session_src
    # The statement must NOT be back in the every-startup list.
    _isolated = session_src[session_src.index("def _apply_isolated_ddl"):
                            session_src.index("def _apply_once")]
    assert "UPDATE price_alerts" not in _isolated, \
        "a row-mutating statement is back in the re-run-on-every-startup list"


def test_a_missing_flow_side_is_unknown_not_total_dominance():
    """EF-05. `(ask_prem or 0.0)` collapsed an ABSENT side into a measured zero, so ask=100
    with bid missing returned ("ask", 1.0) — the most confident answer possible, from a feed
    that reported one number."""
    fn = _load(_SCHED, "_classify_flow_side", {"_FLOW_SIDE_MIN_IMBALANCE": 0.60})
    assert fn(100.0, None)[0] == "unknown"
    assert fn(None, 100.0)[0] == "unknown"
    assert fn(None, None)[0] == "unknown"
    # A genuine measured zero is still a classification, not an absence.
    assert fn(100.0, 0.0)[0] == "ask"


def test_an_unknown_side_produces_no_candidate():
    body = _code(_fn_src(_SCHED, "check_options_flow_alerts"))
    assert 'if _side in ("neutral", "unknown"):' in body
    assert "ask = row.total_ask_side_prem or 0.0" not in body


# ── Residuals the follow-up review raised against the EA fixes ───────────────

def test_the_breakout_entry_must_also_have_upside():
    """EA-02 residual. The plan offers three ways in, and only the two pullback entries were
    checked against the target. A take-profit at or below the BREAKOUT level instructs a buy at
    a price the same plan says to sell at."""
    g = _load(_SCHED, "_plan_geometry_ok")
    ok, why = g({"entry1": 98.5, "entry2": 96.5, "breakout": 115.0,
                 "stop": 94.5, "take_profit": 112.0, "current_price": 100.0})
    assert ok is False and "breakout" in why


def test_the_validator_does_not_claim_a_reward_risk_check_it_never_makes():
    """A comment here promised a reward/risk floor that the code did not enforce, and the
    follow-up review caught the mismatch. No ratio is enforced — the style parameters set the
    levels, and a floor would suppress whole horizons rather than fix them — so the comment
    now says that."""
    body = _fn_src(_SCHED, "_plan_geometry_ok")
    assert "No minimum ratio is enforced" in body


def test_sector_rotation_does_not_consume_its_state_on_a_failed_send():
    """EA-07's SIBLING, which the original fix did not touch. The emerging set was written
    before any delivery, so a total failure left the next weekly run seeing no new sectors."""
    body = _code(_fn_src(_SCHED, "check_sector_rotation_alerts"))
    assert "def _resync_sector_state(" in body
    assert "exclude=set(newly_emerging)" in body
    assert "sector_rotation_alert.new_sectors_not_recorded" in _SCHED


def test_sector_rotation_still_records_fade_outs_when_nothing_newly_emerged():
    """The regression my first attempt at the above introduced: moving the write below the send
    loop put it after the early return, so a sector leaving the set was never removed and could
    never re-alert. Both paths call the resync."""
    body = _code(_fn_src(_SCHED, "check_sector_rotation_alerts"))
    guard = body[body.index("if not newly_emerging:"):]
    guard = guard[:guard.index("with SessionLocal()")]
    assert "_resync_sector_state()" in guard


def test_the_dark_pool_side_is_labelled_an_inference():
    """EA-09's other half, which the first pass left untouched: the template called an inferred
    aggressor side a measured fact. The print and its price are reported; the side is derived
    from where the block landed in the spread."""
    assert "measured fact, not a forecast" not in _EMAIL
    assert "inference, not a measured fact" in _EMAIL
    # The existing disclaimers must survive the rewording.
    assert "aggressor" in _EMAIL.lower()
    assert "not a forecast" in _EMAIL


def test_the_flow_digest_carries_an_unsubscribe_footer():
    """It called raw send_email, so the type it now advertises as manageable arrived with no
    way to act on that. Registering a preference and omitting the control is half a fix."""
    body = _code(_fn_src(_SCHED, "send_flow_digest"))
    assert '_with_unsub(u.email, "flow_digest", html, text)' in body


def test_no_exception_name_is_ever_read_outside_its_own_handler():
    """EF-01 GENERALISED, across the whole scheduler.

    `except ... as NAME` deletes NAME when the handler exits, so any later read raises
    UnboundLocalError — and in an error path that means the recovery code fails, which is how
    EF-01 took down a whole recipient batch. This file has ~40 such handlers; checking only the
    one that broke would leave the other thirty-nine.

    Scoped per function and per handler span, so the many legitimate uses INSIDE handlers pass.
    """
    import ast

    tree = ast.parse(_SCHED)
    offenders = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        spans = [(h.lineno, h.end_lineno, h.name)
                 for n in ast.walk(fn) if isinstance(n, ast.Try)
                 for h in n.handlers if h.name]
        bound = {nm for _, _, nm in spans}
        for node in ast.walk(fn):
            if (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                    and node.id in bound):
                if not any(a <= node.lineno <= b and nm == node.id for a, b, nm in spans):
                    offenders.append(f"{fn.name}:{node.lineno} reads {node.id}")
    assert not offenders, (
        "an exception name is read after its handler exited; Python has already deleted it, so "
        "this raises UnboundLocalError at exactly the moment something has gone wrong:\n  "
        + "\n  ".join(offenders))
