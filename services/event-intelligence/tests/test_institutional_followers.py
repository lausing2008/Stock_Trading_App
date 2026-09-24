"""AUD-INSTFOLLOW: tests for get_institutional_followers().

WHAT THIS REPORT CAN EASILY GET WRONG, and what these tests hold in place.

A "follow the famous funds" page is the easiest thing on this platform to make dishonest by
accident, because every mistake makes it look MORE authoritative, not less. Three were caught in
the first live run and are pinned here so they cannot come back:

  1. RAW RETURNS ARE MOSTLY BETA. The first run reported all sixteen managers negative, from
     -0.35% to -16.66%, which reads as a damning verdict on professional investors. SPY fell
     2.44% over the identical window. Duquesne's "-0.35%" was actually +2.1pp of alpha. Ranking
     on the raw number inverts the top of the table.

  2. A FIELD THAT IS ALWAYS ZERO IS NOT A MEASUREMENT. `price_on_report` and `price_on_filing`
     come back IDENTICAL on every row (278 of 278 Citadel buys), so an "already moved between
     quarter-end and filing" figure computes to 0.00% for every fund, forever. Publishing it
     would assert the disclosure lag costs nothing — a stronger and more wrong claim than
     omitting it. It is deliberately absent, and a test keeps it absent.

  3. UW RETURNS ONE ROW PER SECURITY LINE, so tickers repeat. Undeduped, Citadel read as 300
     buys instead of 180 and single holdings were measured several times.

The service is exercised against a stubbed session and a stubbed UW fetch, so none of this
needs a network call or a live DB.
"""
from types import SimpleNamespace
from unittest.mock import patch

from src.services import institutional as I


def _act(ticker, units_change, filing="2026-08-14", report="2026-06-30",
         px_report=None, px_filing=None):
    return {
        "ticker": ticker, "units_change": units_change,
        "filing_date": filing, "report_date": report,
        "price_on_report": px_report, "price_on_filing": px_filing,
        "security_type": "Share",
    }


class _FakeSession:
    """Serves the ticker map, the benchmark row, and one aggregate row per measured fund.

    Queries are told apart by their BOUND PARAMETERS, not by their SQL text: this suite stubs
    sqlalchemy, so `text()` yields a MagicMock whose str() contains no SQL at all. Matching on
    text silently routed the benchmark query to the aggregate branch and made alpha read None —
    a test-harness bug that looked exactly like a missing feature."""

    def __init__(self, agg=None, tickers=("AAA", "BBB", "CCC"), bench=(100.0, 97.56),
                 agg_by_filing=None, bench_by_filing=None):
        self._agg = agg or SimpleNamespace(n=10, avg_pct=-2.0, pct_up=40.0)
        self._tickers = tickers
        self._bench = bench
        # Per-filing-date overrides, so a test can give two funds DIFFERENT raw returns and
        # DIFFERENT benchmarks — the only way to tell a raw-return ranking from an alpha one.
        self._agg_by_filing = agg_by_filing or {}
        self._bench_by_filing = bench_by_filing or {}

    def execute(self, stmt, params=None):
        # select(Stock.id, Stock.symbol) — the only statement issued with no parameters.
        if params is None:
            return SimpleNamespace(
                all=lambda: [(i + 1, t) for i, t in enumerate(self._tickers)]
            )
        if "fd" in params:  # the SPY benchmark lookup
            b = self._bench_by_filing.get(params["fd"], self._bench)
            if b is None:
                return SimpleNamespace(one_or_none=lambda: None)
            close, fwd = b
            return SimpleNamespace(one_or_none=lambda: SimpleNamespace(close=close, fwd=fwd))
        fdates = params.get("fdates") or []
        agg = self._agg_by_filing.get(fdates[0] if fdates else None, self._agg)
        return SimpleNamespace(one=lambda: agg)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _run(activity_by_fund, agg=None, tracked=None, tickers=("AAA", "BBB", "CCC"),
         bench=(100.0, 97.56), agg_by_filing=None, bench_by_filing=None, **kwargs):
    sess = _FakeSession(agg, tickers=tickers, bench=bench,
                        agg_by_filing=agg_by_filing, bench_by_filing=bench_by_filing)
    tracked = tracked or [("Test Fund", "TEST FUND LLC")]
    with patch.object(I, "SessionLocal", lambda: sess), \
         patch.object(I, "_TRACKED_INSTITUTIONS", tracked), \
         patch.object(I, "_uw_institution_activity",
                      lambda name, limit=500: activity_by_fund.get(name, [])):
        return I.get_institutional_followers(**kwargs)


# ── Benchmark relativity: the defect that inverted the first run ──────────────

def test_alpha_subtracts_the_markets_own_move_over_the_same_window():
    """SPY -2.44% over the window; a fund at -2.00% raw is +0.44pp of alpha, not a loser."""
    res = _run({"TEST FUND LLC": [_act("AAA", 100), _act("BBB", 100)]})
    f = res["funds"][0]
    assert f["avg_21d_pct"] == -2.0
    assert f["benchmark_21d_pct"] == -2.44
    assert f["alpha_vs_spy_pct"] == 0.44


def test_funds_are_ranked_on_alpha_not_on_the_raw_return():
    """The two orderings are deliberately INVERTED here, because a test where they agree cannot
    detect a sort on the wrong column — and an earlier version of this test did exactly that,
    passing while the code ranked on the raw return.

        Skilled:  raw -5.0% against a market that fell 10.0%  ->  alpha +5.0%
        Lucky:    raw -1.0% against a market that was flat    ->  alpha -1.0%

    By raw return Lucky looks better. By alpha Skilled is better, and alpha is the real one."""
    res = _run(
        {"A LLC": [_act("AAA", 1, filing="2026-08-14")],
         "B LLC": [_act("BBB", 1, filing="2026-05-15")]},
        tracked=[("Skilled", "A LLC"), ("Lucky", "B LLC")],
        agg_by_filing={
            "2026-08-14": SimpleNamespace(n=10, avg_pct=-5.0, pct_up=40.0),
            "2026-05-15": SimpleNamespace(n=10, avg_pct=-1.0, pct_up=40.0),
        },
        bench_by_filing={"2026-08-14": (100.0, 90.0), "2026-05-15": (100.0, 100.0)},
    )
    by_name = {f["name"]: f for f in res["funds"]}
    assert by_name["Skilled"]["avg_21d_pct"] == -5.0
    assert by_name["Skilled"]["alpha_vs_spy_pct"] == 5.0
    assert by_name["Lucky"]["avg_21d_pct"] == -1.0
    assert by_name["Lucky"]["alpha_vs_spy_pct"] == -1.0
    assert [f["name"] for f in res["funds"]] == ["Skilled", "Lucky"]


def test_the_benchmark_is_reported_so_a_reader_can_check_the_subtraction():
    res = _run({"TEST FUND LLC": [_act("AAA", 1)]})
    assert res["funds"][0]["benchmark_21d_pct"] == -2.44


# ── The metric that is always zero, and must stay absent ──────────────────────

def test_no_already_moved_field_is_published():
    """UW reports price_on_report and price_on_filing identically, so any such figure is 0.00%
    for every fund forever. A zero here would read as "the lag costs nothing", which is false."""
    res = _run({"TEST FUND LLC": [_act("AAA", 1, px_report=100.0, px_filing=100.0)]})
    assert "already_moved_pct" not in res["funds"][0]


def test_identical_report_and_filing_prices_do_not_become_a_zero_cost_claim():
    res = _run({"TEST FUND LLC": [_act("AAA", 1, px_report=50.0, px_filing=50.0)]})
    blob = " ".join(res["caveats"]).lower()
    assert "already" not in blob or "moved" not in blob


# ── Deduplication ─────────────────────────────────────────────────────────────

def test_a_ticker_repeated_across_security_lines_counts_once():
    """Undeduped, Citadel read as 300 buys instead of 180."""
    res = _run({"TEST FUND LLC": [_act("AAA", 100), _act("AAA", 100), _act("BBB", 50)]})
    assert res["funds"][0]["n_buys"] == 2


def test_buys_and_sells_are_split_by_the_sign_of_units_change():
    res = _run({"TEST FUND LLC": [_act("AAA", 100), _act("BBB", -100), _act("CCC", 0)]})
    f = res["funds"][0]
    assert f["n_buys"] == 1
    assert f["n_sells"] == 1  # a zero change is neither


# ── Mixing quarters ───────────────────────────────────────────────────────────

def test_only_the_latest_filing_is_measured_never_two_quarters_averaged_together():
    """Returns entered on different dates cannot be averaged into one figure — the result would
    describe no window that ever existed."""
    res = _run({"TEST FUND LLC": [
        _act("AAA", 1, filing="2026-08-14", report="2026-06-30"),
        _act("BBB", 1, filing="2026-05-15", report="2026-03-31"),
        _act("CCC", 1, filing="2026-08-14", report="2026-06-30"),
    ]})
    f = res["funds"][0]
    assert f["filing_date"] == "2026-08-14"
    assert f["n_buys"] == 2


# ── Staleness and availability ────────────────────────────────────────────────

def test_each_fund_carries_its_own_dates_and_staleness():
    """Coverage varies enormously — UW's Scion data is ~359 days old while most managers are
    current — so a single "as of" header on the page would be a lie for some rows."""
    res = _run({"TEST FUND LLC": [_act("AAA", 1)]})
    f = res["funds"][0]
    assert f["report_date"] == "2026-06-30"
    assert f["filing_date"] == "2026-08-14"
    assert f["disclosure_lag_days"] == 45
    assert f["staleness_days"] is not None and f["staleness_days"] > 0


def test_a_fund_with_no_data_is_reported_as_unavailable_not_silently_dropped():
    """A missing manager must be visible as missing; dropping the row makes the table look
    complete when it is not."""
    res = _run({}, tracked=[("Ghost Fund", "GHOST LLC")])
    f = res["funds"][0]
    assert f["unavailable"] is True
    assert f["avg_21d_pct"] is None
    assert f["sample_is_adequate"] is False


def test_unavailable_and_unmeasurable_funds_sort_below_the_measured_ones():
    res = _run(
        {"REAL LLC": [_act("AAA", 1)]},
        tracked=[("Ghost", "GHOST LLC"), ("Real", "REAL LLC")],
    )
    assert res["funds"][0]["name"] == "Real"


# ── Sample floor ──────────────────────────────────────────────────────────────

def test_adequacy_is_decided_by_positions_actually_PRICED_not_positions_reported():
    """A fund can report 349 adds of which we price 15. The reported count is not the sample."""
    res = _run({"TEST FUND LLC": [_act(f"T{i}", 1) for i in range(50)]},
               agg=SimpleNamespace(n=3, avg_pct=-2.0, pct_up=33.0),
               tickers=tuple(f"T{i}" for i in range(50)))
    f = res["funds"][0]
    assert f["n_buys"] == 50
    assert f["n_measured"] == 3
    assert f["sample_is_adequate"] is False


def test_the_floor_actually_used_is_reported_back():
    res = _run({"TEST FUND LLC": [_act("AAA", 1)]}, min_positions=25)
    assert res["min_positions_for_adequacy"] == 25
    assert res["funds"][0]["sample_is_adequate"] is False


# ── Framing ───────────────────────────────────────────────────────────────────

def test_entry_basis_is_the_filing_date_not_the_quarter_end():
    """The quarter-end the filing describes was never reachable by anyone outside the fund."""
    res = _run({"TEST FUND LLC": [_act("AAA", 1)]})
    assert res["entry_basis"] == "filing_date"


def test_the_caveats_state_that_this_is_a_snapshot_and_not_a_track_record():
    res = _run({"TEST FUND LLC": [_act("AAA", 1)]})
    blob = " ".join(res["caveats"]).lower()
    assert "snapshot" in blob
    assert "not a track record" in blob


def test_the_caveats_warn_that_a_13f_shows_only_the_long_book():
    """For market-neutral multi-strats a large negative alpha on the disclosed longs may be the
    hedge working. Without this the page reads as a verdict on Citadel and Millennium."""
    blob = " ".join(_run({"TEST FUND LLC": [_act("AAA", 1)]})["caveats"]).lower()
    assert "long" in blob
    assert "hedge" in blob or "short" in blob


def test_alpha_is_omitted_rather_than_guessed_when_the_benchmark_is_unavailable():
    """No SPY history for the window means no honest alpha. Reporting the raw return AS alpha
    would silently relabel beta as skill."""
    res = _run({"TEST FUND LLC": [_act("AAA", 1)]}, bench=None)
    f = res["funds"][0]
    assert f["avg_21d_pct"] == -2.0
    assert f["alpha_vs_spy_pct"] is None
    assert f["benchmark_21d_pct"] is None


def test_a_fund_unreachable_upstream_does_not_take_the_whole_report_down():
    """The measured and unavailable rows were once two hand-written dicts; the unavailable one
    lacked `alpha_vs_spy_pct`, which the ranking sort reads unconditionally, so ONE unreachable
    fund raised KeyError and returned nothing for the other fifteen."""
    res = _run({"REAL LLC": [_act("AAA", 1)]},
               tracked=[("Ghost", "GHOST LLC"), ("Real", "REAL LLC")])
    assert len(res["funds"]) == 2
    assert {f["name"] for f in res["funds"]} == {"Ghost", "Real"}
    ghost = next(f for f in res["funds"] if f["name"] == "Ghost")
    real = next(f for f in res["funds"] if f["name"] == "Real")
    # Every key present on a measured row must exist on the unavailable one too.
    assert set(ghost) == set(real)
