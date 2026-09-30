"""AUD-ANTICHASE-FUNNEL: count the anti-chase gate's AUTHORITATIVE rejections, not its opinions.

WHY THIS IS NOT JUST A COUNTER. `_should_enter()` runs on every candidate — but when the decision
engine is primary and reachable, `gate_source == "de"` and `_should_enter()`'s verdict is only a
shadow comparison. Tallying its anti-chase rejection there would record a block that never
happened, which is worse than no number at all.

So three counters, deliberately separate:

    anti_chase_reached                 candidates that got as far as the gate
    anti_chase_rejected                the gate said no
    anti_chase_rejected_authoritative  the gate said no AND that decided the entry

`reached` is also the honest denominator. The checkpoint measured that 30.1% of BUY SIGNALS carry
`roc_10 >= 10` against a predicted ~17% incremental BLOCK rate — not comparable, because the gate
sits behind the watchlist, conviction and every earlier gate. `reached` is that population.

None of this establishes incremental lost trades or profitability; that needs the counterfactual
outcomes of vetoed candidates.
"""
import pathlib

PT_SRC = (pathlib.Path(__file__).resolve().parents[1]
          / "src/services/paper_trading_engine.py").read_text()


def _fn_src(name: str) -> str:
    i = PT_SRC.index(f"def {name}")
    nxt = PT_SRC.find("\ndef ", i + 10)
    return PT_SRC[i:nxt if nxt != -1 else len(PT_SRC)]


def _funnel_block() -> str:
    i = PT_SRC.index("AUD-ANTICHASE-FUNNEL: three counters")
    return PT_SRC[i:PT_SRC.index("if not should_enter:", i)]


# ── the producer side: _should_enter reports what it saw ────────────────────────────────

def _run_gate(roc_10, threshold=10.0, with_telemetry=True):
    """Execute the REAL anti-chase branch, extracted from the engine.

    Lifted and executed rather than described: an earlier fix in this session shipped a test that
    reimplemented the logic it was checking, and two sabotages walked straight through it.

    The extracted block contains a `return`, which is a SyntaxError at module level — so it is
    wrapped back into a function here. Returning `True` from that wrapper means the gate rejected.
    """
    src = _fn_src("_should_enter")
    i = src.index('_roc10_paper = reasons.get("roc_10")')
    j = src.index("# T220-D: Economic calendar blackout", i)
    body = "\n".join("    " + line[4:] if line.startswith("    ") else "    " + line.lstrip()
                     for line in src[i:j].splitlines())
    tele = {} if with_telemetry else None
    env = {"reasons": {"roc_10": roc_10} if roc_10 is not None else {},
           "_MAX_ROC10_FOR_ENTRY_PAPER": threshold, "telemetry": tele}
    exec(compile("def _gate():\n" + body + "\n    return None", "<gate>", "exec"), env)
    result = env["_gate"]()
    # The production branch returns the (False, -99, [...]) reject tuple; anything else is a pass.
    rejected = isinstance(result, tuple) and result[0] is False
    return tele, rejected


def test_a_candidate_below_the_threshold_reaches_but_is_not_rejected():
    tele, rejected = _run_gate(4.0)
    assert tele["reached_anti_chase"] is True
    assert tele["roc_10"] == 4.0
    assert "anti_chase_rejected" not in tele
    assert rejected is False


def test_a_candidate_at_the_threshold_is_rejected():
    """The boundary is `>=`, matching the deployed gate."""
    tele, rejected = _run_gate(10.0)
    assert tele["reached_anti_chase"] is True
    assert rejected is True


def test_a_candidate_above_the_threshold_is_rejected():
    tele, rejected = _run_gate(25.0)
    assert rejected is True


def test_a_rejection_SETS_THE_TELEMETRY_FLAG_not_only_the_return_value():
    """The counters downstream read the FLAG, not the return value — so the flag is what has to be
    right. An earlier version of this file inferred rejection solely from the returned tuple, and
    a sabotage that deleted the flag assignment passed every test while silently zeroing the
    authoritative-rejection count in production."""
    for roc in (10.0, 25.0):
        tele, rejected = _run_gate(roc)
        assert rejected is True
        assert tele.get("anti_chase_rejected") is True, (
            f"roc_10={roc} rejected but the telemetry flag the counters read was never set")


def test_the_flag_is_absent_when_the_gate_passes():
    """The mirror: a pass must not set it, or every candidate reaching the gate would be counted
    as blocked."""
    tele, rejected = _run_gate(4.0)
    assert rejected is False
    assert "anti_chase_rejected" not in tele


def test_a_candidate_with_no_roc_does_not_count_as_reaching():
    """A missing `roc_10` means the gate had nothing to evaluate. Counting it as 'reached' would
    inflate the denominator and understate the block rate."""
    tele, _ = _run_gate(None)
    assert "reached_anti_chase" not in tele


def test_telemetry_is_optional_and_absent_by_default():
    """Historical replay and other callers pass no telemetry; the gate must behave identically."""
    tele, _ = _run_gate(25.0, with_telemetry=False)
    assert tele is None


def test_the_signature_keeps_telemetry_optional():
    """Widening the return tuple would break every other caller, including replay. The
    out-parameter must stay opt-in."""
    sig = PT_SRC[PT_SRC.index("def _should_enter("):PT_SRC.index("-> tuple[bool, int, list[str]]")]
    assert "telemetry: dict | None = None" in sig


# ── the consumer side: only an authoritative rejection is counted as one ────────────────

def test_authoritative_rejections_require_should_enter_to_have_decided():
    """THE POINT. `gate_source == "de"` means this function was a shadow comparison."""
    block = _funnel_block()
    assert 'gate_source in ("fallback", "legacy")' in block
    assert "anti_chase_rejected_authoritative" in block


def test_a_shadow_rejection_is_counted_but_kept_apart():
    """Not discarded — a shadow rejection is real information about disagreement between the two
    scorers. It simply must not be reported as a block."""
    block = _funnel_block()
    assert "anti_chase_rejected_shadow_only" in block


def test_the_reached_denominator_is_broken_out_by_decision_source():
    block = _funnel_block()
    assert "anti_chase_reached" in block
    assert "anti_chase_reached_via_" in block


def test_the_three_counters_are_distinct_keys():
    block = _funnel_block()
    for key in ("anti_chase_reached", "anti_chase_rejected",
                "anti_chase_rejected_authoritative"):
        assert f'"{key}"' in block


def test_the_authoritative_block_is_logged_with_its_decision_source():
    """A count with no way to audit individual cases is hard to trust later."""
    block = _funnel_block()
    i = block.index("anti_chase_rejected_authoritative")
    assert "decision_source=gate_source" in block[i:i + 600]
