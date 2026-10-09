"""O02 — the migrated chain made route premiums zero and whale flags false.

END TO END THROUGH THE REAL ADAPTER, which is the point: the existing route tests use
yfinance-SHAPED fixtures, and those carry a genuine `lastPrice`. The mismatch only appears when
the actual UW adapter's output is fed through, so these tests start at `_to_chain_row`.

THE DEFECT. `uw_option_chain._to_chain_row` sets `last_price = 0.0` because that archive has no
last-trade field. `get_options_flow` computed `premium = volume * lastPrice * 100` — zero for
every UW-derived row — and then `is_whale = premium > 500_000`, false for all of them regardless
of activity. False reads as "checked, not a whale". That count feeds the options-pressure score
AND is persisted as the ML feature `opt_whale_count`, so a missing input was trained on as a
measured zero.

Substituting a midpoint does not fix it: volume x mark x 100 is a marked TURNOVER ESTIMATE over
a day of aggregated trades, not premium anyone paid. Three quantities, three fields.
"""
import importlib.util
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "shared"))

_UW = pathlib.Path(__file__).resolve().parents[1] / "src/services/uw_option_chain.py"
_spec = importlib.util.spec_from_file_location("uw_chain_under_test", _UW)
uwc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(uwc)


def _uw_row(**over):
    """A row exactly as the UW archive supplies it."""
    return {"strike": 100.0, "option_type": "call", "nbbo_bid": 4.9, "nbbo_ask": 5.1,
            "volume": 4000, "open_interest": 1000, "implied_volatility": 0.42, **over}


# ---- the adapter states the absence rather than implying it ---------------------------------

def test_the_adapter_says_the_last_price_is_unavailable():
    row = uwc._to_chain_row(_uw_row(), spot=101.0)
    assert row["last_price"] == 0.0
    assert row["last_price_available"] is False, \
        "a zero price and an absent price are different facts; only one is a measurement"


def test_the_adapter_still_carries_a_usable_two_sided_quote():
    """The control: the absence is specific to the last trade, not to the whole row."""
    row = uwc._to_chain_row(_uw_row(), spot=101.0)
    assert row["bid"] == 4.9 and row["ask"] == 5.1 and row["volume"] == 4000


# ---- the route's own computation, exercised directly ----------------------------------------

# THE REAL FUNCTIONS, extracted from source and executed — not described.
#
# A sabotage run caught the first version of this file RE-IMPLEMENTING the premium rule: two
# deliberate corruptions of the real code (substituting a midpoint as traded premium, and
# scoring an unavailable whale count as zero) both passed, because the helper computed its own
# answer and the remaining checks matched source text loosely enough to hit the wrong line.
# routes.py cannot be imported in this environment, so this uses the extraction pattern already
# established by test_options_pressure_score.py.
_ROUTES_SRC = (pathlib.Path(__file__).resolve().parents[1] / "src/api/routes.py").read_text()


def _extract(name: str):
    start = _ROUTES_SRC.index(f"def {name}(")
    rest = _ROUTES_SRC[start:]
    end = len(rest)
    for marker in ("\ndef ", "\nclass ", "\n@"):
        i = rest.find(marker, 1)
        if i != -1:
            end = min(end, i)
    ns: dict = {}
    exec(compile(rest[:end], f"<{name}>", "exec"), ns)
    return ns[name]


unusual_premium_fields = _extract("unusual_premium_fields")
whale_coverage = _extract("whale_coverage")
compute_options_pressure_score = _extract("compute_options_pressure_score")


def _premium_fields(last_price, *, available, bid=4.9, ask=5.1, vol=4000):
    return unusual_premium_fields(vol, last_price, bid, ask, last_price_available=available)


def test_a_uw_row_yields_no_traded_premium_and_an_UNKNOWN_whale_flag():
    """THE WITNESS. Previously: premium 0.0, is_whale False."""
    adapted = uwc._to_chain_row(_uw_row(), spot=101.0)
    got = _premium_fields(adapted["last_price"],
                          available=adapted["last_price_available"],
                          bid=adapted["bid"], ask=adapted["ask"], vol=adapted["volume"])
    assert got["traded_premium"] is None, "unavailable, not zero"
    assert got["is_whale"] is None, "unknown, not false — false says it was checked"
    assert got["premium_basis"] == "estimated_turnover"


def test_the_estimate_is_offered_but_never_called_premium():
    adapted = uwc._to_chain_row(_uw_row(), spot=101.0)
    got = _premium_fields(adapted["last_price"], available=adapted["last_price_available"],
                          bid=adapted["bid"], ask=adapted["ask"], vol=adapted["volume"])
    # 4000 contracts x $5.00 mark x 100 = $2,000,000 of marked turnover...
    assert got["estimated_turnover"] == 2_000_000.0
    # ...and that is NOT premium, so it cannot make a whale.
    assert got["traded_premium"] is None and got["is_whale"] is None


def test_a_genuine_tape_price_still_produces_a_real_premium_and_whale():
    """The control. The fix must not blind the path that does have the data."""
    got = _premium_fields(2.0, available=True, vol=4000)
    assert got["traded_premium"] == 800_000.0
    assert got["is_whale"] is True and got["premium_basis"] == "tape"


def test_a_contract_that_really_traded_at_zero_is_not_the_same_as_no_tape_field():
    """Both give no premium, but only one of them was measured."""
    absent = _premium_fields(0.0, available=False)
    assert absent["premium_basis"] == "estimated_turnover"
    assert absent["is_whale"] is None


# ---- the pressure-score consumer -------------------------------------------------------------

def test_the_pressure_score_does_not_treat_an_unavailable_count_as_zero():
    """The score is read as conviction/intensity. Scoring an unmeasured component as zero is
    indistinguishable from "we looked and found none"."""
    import ast
    src = (pathlib.Path(__file__).resolve().parents[1] / "src/api/routes.py").read_text()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "compute_options_pressure_score")
    body = ast.unparse(fn)
    assert "whale_count is None" in body, "the unavailable case must be handled explicitly"
    assert "whale_pts_unavailable" in body, "and disclosed to the reader"
    assert "max_possible" in body, \
        "40 out of 70 must not read as 40 out of 100 when a component could not be measured"


def test_the_whale_count_payload_carries_everything_needed_to_read_it():
    """Superseded by executing `whale_coverage` directly above; this pins the PAYLOAD contract
    so a field cannot be dropped from the response while the function still returns it."""
    got = whale_coverage([unusual_premium_fields(4000, 0.0, 4.9, 5.1,
                                                 last_price_available=False)])
    for key in ("whale_count", "whale_count_unknown", "whale_count_assessed",
                "whale_count_total", "whale_count_basis", "top_whale_premium",
                "top_estimated_turnover"):
        assert key in got, key


def test_the_route_calls_the_shared_function_rather_than_inlining_the_rule():
    """The rule lived inline in `get_options_flow`, which is why a test could only describe it.
    Pinning the CALL is what stops the inline version coming back beside the shared one."""
    import ast
    tree = ast.parse(_ROUTES_SRC)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "get_options_flow")
    body = ast.unparse(fn)
    assert "unusual_premium_fields(" in body
    # The arithmetic must not be duplicated here. Counted as CALLS to the shared function rather
    # than matched as text: the repo's T401 ratchet is right that a substring check pinning a
    # number passes when the number changes, so the structure is what gets asserted.
    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "unusual_premium_fields"]
    assert len(calls) == 1, "exactly one place computes these quantities"
    # No dict in this function may name a premium field itself — they arrive via `**_pf`, so
    # an inline reintroduction shows up as a literal key. (Matching `* 100` was too broad: the
    # implied-volatility percent conversion is also a multiply by 100 and is unrelated.)
    inline_keys = {k.value for d in ast.walk(fn) if isinstance(d, ast.Dict)
                   for k in d.keys if isinstance(k, ast.Constant)
                   and k.value in ("premium", "traded_premium", "estimated_turnover",
                                   "is_whale", "premium_basis")}
    assert not inline_keys, \
        f"premium fields computed inline beside the shared function: {sorted(inline_keys)}"


def test_the_adapter_flag_reaches_the_route_dataframe():
    """`.fillna(0)` downstream turns a null into a number, so the fact must travel as its own
    column — dropping it in `_df` is how the route would silently lose the distinction."""
    import ast
    src = (pathlib.Path(__file__).resolve().parents[1] / "src/api/routes.py").read_text()
    code = ast.unparse(ast.parse(src))
    assert "'last_price_available': r.get('last_price_available', True)" in code
    assert "'last_price_available'," in code or "'inTheMoney', 'last_price_available'" in code


# ---- the two cases a re-implementing test could not see --------------------------------------

def test_a_midpoint_is_never_returned_as_traded_premium():
    """SABOTAGE S2. Substituting the mark where the tape is missing relabels the same
    unavailability — and the first version of this file could not see it, because the helper
    computed its own answer instead of calling the real function."""
    got = unusual_premium_fields(4000, 0.0, 4.9, 5.1, last_price_available=False)
    assert got["traded_premium"] is None
    assert got["estimated_turnover"] == 2_000_000.0
    assert got["traded_premium"] != got["estimated_turnover"]
    assert got["is_whale"] is None, "an estimate can never make a whale"


def test_an_unavailable_whale_count_scores_zero_points_AND_lowers_the_maximum():
    """SABOTAGE S3. `(whale_count or 0) * 10` also gives zero points — the difference is that
    it reports the score out of 100 as though the component had been measured and found absent.
    Executing the real function is what separates the two."""
    unavailable = compute_options_pressure_score(
        cp_ratio=3.0, sentiment="bullish", whale_count=None,
        total_call_vol=5000, total_put_vol=1000)
    measured_none = compute_options_pressure_score(
        cp_ratio=3.0, sentiment="bullish", whale_count=0,
        total_call_vol=5000, total_put_vol=1000)
    assert unavailable["score"] == measured_none["score"], "both contribute zero points"
    # ...but only one of them claims the component was measured.
    assert unavailable["components"]["whale_pts_unavailable"] is True
    # 40 (cp) + 10 (volume), with whale and GEX unmeasured.
    assert unavailable["components"]["max_possible"] == 50.0
    assert "whale_pts_unavailable" not in measured_none["components"]
    # The denominator is ALWAYS stated — a score without one cannot be read at all — and it is
    # larger here because the whale component WAS measured and happened to be zero.
    assert measured_none["components"]["max_possible"] == 80.0


def test_a_measured_whale_count_still_scores_normally():
    got = compute_options_pressure_score(
        cp_ratio=3.0, sentiment="bullish", whale_count=2,
        total_call_vol=5000, total_put_vol=1000)
    assert got["components"]["whale_pts"] == 20.0
    assert got["components"]["max_possible"] == 80.0
    assert "whale" in got["components"]["measured"]


# ---- partial coverage, and score comparability ----------------------------------------------

def test_a_contract_with_no_tape_premium_is_never_counted_as_a_non_whale():
    """PARTIAL COVERAGE. A plain `sum(1 for c in unusual if c["is_whale"])` counts an unknown as
    a non-whale, which is the same falsy-zero error one level up."""
    unusual = [
        unusual_premium_fields(4000, 200.0, 4.9, 5.1),                       # tape: whale
        unusual_premium_fields(4000, 1.0, 4.9, 5.1),                         # tape: not a whale
        unusual_premium_fields(4000, 0.0, 4.9, 5.1, last_price_available=False),  # unknown
    ]
    got = whale_coverage(unusual)   # THE REAL FUNCTION, not a count computed here
    assert got["whale_count"] == 1
    assert got["whale_count_assessed"] == 2 and got["whale_count_total"] == 3
    assert got["whale_count_unknown"] == 1
    assert "PARTIAL" in got["whale_count_basis"]
    assert "neither counted as whales nor as non-whales" in got["whale_count_basis"]


def test_nothing_assessable_gives_a_null_count_not_a_zero():
    got = whale_coverage([unusual_premium_fields(4000, 0.0, 4.9, 5.1,
                                                 last_price_available=False)])
    assert got["whale_count"] is None
    assert got["whale_count_assessed"] == 0 and got["whale_count_total"] == 1
    assert got["whale_count_basis"].startswith("unavailable")


def test_full_coverage_says_complete_rather_than_partial():
    got = whale_coverage([unusual_premium_fields(4000, 200.0, 4.9, 5.1),
                          unusual_premium_fields(4000, 1.0, 4.9, 5.1)])
    assert got["whale_count"] == 1 and got["whale_count_unknown"] == 0
    assert "complete" in got["whale_count_basis"] and "PARTIAL" not in got["whale_count_basis"]
    assert got["top_whale_premium"] == 80_000_000.0


def test_the_route_uses_the_shared_counting_rule():
    """The counting lived inline, which is how a test could only describe it. Pinning the CALL
    is what stops the collapsed `sum(1 for c in unusual if c["is_whale"])` coming back."""
    import ast
    fn = next(n for n in ast.walk(ast.parse(_ROUTES_SRC))
              if isinstance(n, ast.FunctionDef) and n.name == "get_options_flow")
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "whale_coverage"]
    assert len(calls) == 1
    inline = {k.value for d in ast.walk(fn) if isinstance(d, ast.Dict)
              for k in d.keys if isinstance(k, ast.Constant)
              and str(k.value).startswith("whale_count")}
    assert not inline, f"whale fields computed inline beside the shared rule: {sorted(inline)}"


def test_the_denominator_reflects_every_unmeasured_component():
    """40 + 30 + 10 = 80, plus GEX 20. The first version of this said "70 + 20 if gex", which
    was wrong arithmetic AND was emitted only when the whale count was missing."""
    full = compute_options_pressure_score(
        cp_ratio=3.0, sentiment="bullish", whale_count=2, total_call_vol=5000,
        total_put_vol=1000, gex={"distance_to_flip_pct": 2.0})
    assert full["components"]["max_possible"] == 100.0
    no_gex = compute_options_pressure_score(
        cp_ratio=3.0, sentiment="bullish", whale_count=2,
        total_call_vol=5000, total_put_vol=1000)
    assert no_gex["components"]["max_possible"] == 80.0, "GEX absent lowers the maximum too"
    no_whale = compute_options_pressure_score(
        cp_ratio=3.0, sentiment="bullish", whale_count=None,
        total_call_vol=5000, total_put_vol=1000)
    assert no_whale["components"]["max_possible"] == 50.0
    neither = compute_options_pressure_score(
        cp_ratio=3.0, sentiment="bullish", whale_count=None,
        total_call_vol=5000, total_put_vol=1000, gex={"distance_to_flip_pct": None})
    assert neither["components"]["max_possible"] == 50.0


def test_the_score_never_emits_a_normalised_percentage():
    """40 out of 50 is not "80% confidence", and a ratio over a denominator that varies with
    what happened to be measurable looks comparable across symbols without being so."""
    got = compute_options_pressure_score(
        cp_ratio=3.0, sentiment="bullish", whale_count=None,
        total_call_vol=5000, total_put_vol=1000)
    for key in got["components"]:
        assert "pct" not in key and "percent" not in key and "confidence" not in key
    assert "score_pct" not in got and "normalised" not in got
    assert "not a percentage and not a probability" in got["components"]["comparability"]


def test_the_measured_set_is_stated_so_two_scores_can_be_compared_honestly():
    a = compute_options_pressure_score(
        cp_ratio=3.0, sentiment="bullish", whale_count=2,
        total_call_vol=5000, total_put_vol=1000)
    b = compute_options_pressure_score(
        cp_ratio=3.0, sentiment="bullish", whale_count=None,
        total_call_vol=5000, total_put_vol=1000)
    assert a["components"]["measured"] == ["cp_ratio", "volume", "whale"]
    assert b["components"]["measured"] == ["cp_ratio", "volume"]
    assert b["components"]["unmeasured"] == ["gex", "whale"]
    assert a["components"]["measured"] != b["components"]["measured"], \
        "these two scores are not comparable, and the payload is what says so"
