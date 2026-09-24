"""AUD-SIGNALCOHORT: telling a reader what THIS class of signal has actually done.

THE DEFECT. The AI Signal email carried a "90d signal accuracy" badge that was a per-SYMBOL win
rate pooling every direction together. BUY and SELL do not merely differ on this platform, they
point opposite ways — measured 2026-09-24 over 18,561 resolved outcomes:

    BUY   n=13,553   avg 5d  -1.22%   (win 39-41% by horizon)
    SELL  n=5,008    avg 5d  +1.03%

So a BUY alert was quoting a figure partly composed of SELL outcomes, which flatters it. The
same badge also admitted cohorts of THREE (`count >= 3`) — a win rate on three observations is
an anecdote wearing a percentage sign, rendered beside real measurements with nothing to tell
them apart.

WHAT THE NEW LINE MUST AND MUST NOT SAY. It is a measured base rate for the (direction, horizon)
class, not a forecast for the stock in hand. That distinction is the only reason it is safe to
show a number this unflattering: a reader who mistook "-1.22%" for a prediction would draw the
wrong conclusion about THIS alert instead of the right one about the class. Hence "averaged",
never "expect".

A note on units, because this repo has been caught by it twice: `signal_outcomes.return_5d` is
a FRACTION (-0.0122 = -1.22%). The conversion happens once, in the helper, so no consumer has
to remember.
"""
from types import SimpleNamespace
from unittest.mock import patch

from src.services.email_service import _cohort_summary_str

# This suite stubs sqlalchemy, so text() yields a MagicMock whose str() contains no SQL at all —
# the executed statement cannot be inspected. The two scoping assertions below therefore read
# the shipped SOURCE of this one function, scoped to it and never the module, and pin no numeric
# literal (AUD-T401-SOURCETEXTTESTS). This is the third place in this codebase where matching on
# SQL TEXT silently passed against a stubbed clause; the source read is the pattern that works.
def _cohort_source() -> str:
    import inspect

    from src.services.scheduler import _signal_cohort_stats
    return inspect.getsource(_signal_cohort_stats)


def _row(n=1000, avg_ret=-0.0122, wins=400):
    return SimpleNamespace(n=n, avg_ret=avg_ret, wins=wins)


class _Sess:
    def __init__(self, row=None, raises=False):
        self._row = row if row is not None else _row()
        self._raises = raises
        self.params_seen = []
        self.sql_seen = []

    def execute(self, sql, params=None):
        if self._raises:
            raise RuntimeError("outcomes unavailable")
        self.params_seen.append(params or {})
        self.sql_seen.append(str(sql))
        return SimpleNamespace(one=lambda: self._row)


def _stats(row=None, raises=False, direction="BUY", horizon="SWING"):
    from src.services.scheduler import _signal_cohort_stats
    sess = _Sess(row, raises)
    # The helper caches in Redis, and a stubbed client returning a truthy Mock from get() would
    # short-circuit the query entirely — so _get_redis() is patched to a client that always
    # misses. Patching _get_redis (the real accessor) rather than a module-level handle also
    # pins that the helper USES it: an earlier draft referenced a module-level `_rc` that does
    # not exist here, and the resulting NameError was swallowed by the cache's own except,
    # leaving the cache permanently dead while every result still looked correct.
    with patch("src.services.scheduler._get_redis", lambda: SimpleNamespace(
        get=lambda *_a, **_k: None, setex=lambda *_a, **_k: None
    )):
        return _signal_cohort_stats(sess, direction, horizon), sess


# ── Units ─────────────────────────────────────────────────────────────────────

def test_the_stored_fraction_is_converted_to_a_percent_exactly_once():
    """-0.0122 is -1.22%, not -0.0122% and not -122%."""
    out, _ = _stats(_row(n=13553, avg_ret=-0.0122, wins=5400))
    assert out["avg_return_5d_pct"] == -1.22


def test_a_positive_cohort_keeps_its_sign():
    out, _ = _stats(_row(n=5008, avg_ret=0.0103, wins=2100), direction="SELL")
    assert out["avg_return_5d_pct"] == 1.03


def test_the_win_rate_is_a_percentage_of_the_cohort():
    out, _ = _stats(_row(n=1000, avg_ret=-0.01, wins=406))
    assert out["win_rate_5d_pct"] == 40.6


# ── The sample floor ──────────────────────────────────────────────────────────

def test_a_cohort_below_the_floor_returns_nothing_rather_than_a_fabricated_rate():
    assert _stats(_row(n=29))[0] is None


def test_a_cohort_at_the_floor_is_reported():
    assert _stats(_row(n=30))[0] is not None


def test_an_empty_cohort_returns_nothing():
    assert _stats(_row(n=0, avg_ret=None, wins=0))[0] is None


def test_a_null_average_is_not_treated_as_zero():
    assert _stats(_row(n=5000, avg_ret=None))[0] is None


# ── Cohort scoping: the actual defect ─────────────────────────────────────────

def test_the_query_is_scoped_to_the_signals_own_direction():
    """The whole point. Pooling BUY with SELL describes neither, and flatters BUY.

    Asserts the SQL actually FILTERS on the direction, not merely that the parameter was bound
    — an earlier version checked only the binding, and passed against a WHERE clause rewritten
    to `:dir IS NOT NULL`, which binds the parameter and filters nothing. The bound value is
    checked too, since a correct clause against the wrong value is just as broken."""
    _, sess = _stats(direction="BUY")
    assert sess.params_seen[0]["dir"] == "BUY"
    assert "signal_direction = :dir" in _cohort_source()


def test_the_query_is_scoped_to_the_signals_own_horizon():
    _, sess = _stats(horizon="GROWTH")
    assert sess.params_seen[0]["hz"] == "GROWTH"
    assert "horizon = :hz" in _cohort_source()


def test_the_horizon_is_normalised_so_casing_cannot_split_one_cohort_in_two():
    _, sess = _stats(horizon="swing")
    assert sess.params_seen[0]["hz"] == "SWING"


def test_a_missing_horizon_falls_back_to_the_direction_wide_cohort():
    """Better a correct direction-wide base rate than no line at all — but it must not silently
    bind an empty horizon and match nothing."""
    out, sess = _stats(horizon=None)
    assert "hz" not in sess.params_seen[0]
    assert out["horizon"] is None


def test_a_missing_direction_yields_nothing_rather_than_pooling_everything():
    assert _stats(direction="")[0] is None
    assert _stats(direction=None)[0] is None


def test_a_failed_query_degrades_to_no_line_rather_than_raising_into_the_alert():
    """A missing badge is a cosmetic loss; an exception here would kill the alert email."""
    assert _stats(raises=True)[0] is None


# ── Rendering ─────────────────────────────────────────────────────────────────

def test_the_line_names_the_class_the_sample_and_both_measurements():
    out, _ = _stats(_row(n=13553, avg_ret=-0.0122, wins=5400))
    line = _cohort_summary_str(out)
    assert "BUY/SWING" in line
    assert "-1.22%" in line
    assert "n=13,553" in line


def test_the_line_says_averaged_not_expect():
    """It is a measured base rate for a class, never a forecast for this stock. A reader who
    mistook a negative number for a prediction would draw the wrong conclusion about THIS
    alert instead of the right one about the class."""
    line = _cohort_summary_str(_stats()[0])
    assert "averaged" in line
    for forecast_word in ("expect", "will ", "predict", "should return"):
        assert forecast_word not in line.lower()


def test_a_positive_cohort_renders_an_explicit_plus_sign():
    out, _ = _stats(_row(n=5008, avg_ret=0.0103, wins=2100), direction="SELL")
    assert "+1.03%" in _cohort_summary_str(out)


def test_no_cohort_renders_a_dash_not_a_zero():
    """A zero would read as "this signal type has historically gone nowhere", which is a claim.
    A dash is the absence of one."""
    assert _cohort_summary_str(None) == "—"
    assert _cohort_summary_str({}) == "—"
    assert _cohort_summary_str({"n": 0, "avg_return_5d_pct": None}) == "—"


def test_the_result_is_cached_so_a_per_alert_call_is_not_a_per_alert_query():
    """check_signal_alerts() calls this once per fired alert. Without a working cache that is a
    full scan of signal_outcomes per email — and a NameError on the cache handle would be
    swallowed by the surrounding except, so nothing would ever surface the regression."""
    from src.services.scheduler import _signal_cohort_stats

    writes = []
    sess = _Sess()
    fake = SimpleNamespace(
        get=lambda *_a, **_k: None,
        setex=lambda k, ttl, v: writes.append((k, ttl)),
    )
    with patch("src.services.scheduler._get_redis", lambda: fake):
        _signal_cohort_stats(sess, "BUY", "SWING")
    assert len(writes) == 1
    assert "signal_cohort:BUY:SWING" in writes[0][0]
    assert writes[0][1] > 0


def test_a_cached_value_is_returned_without_touching_the_database():
    from src.services.scheduler import _signal_cohort_stats

    import json
    payload = {"direction": "BUY", "horizon": "SWING", "n": 99,
               "avg_return_5d_pct": -1.22, "win_rate_5d_pct": 40.0}
    sess = _Sess(raises=True)  # any DB access at all would raise
    fake = SimpleNamespace(get=lambda *_a, **_k: json.dumps(payload), setex=lambda *_a, **_k: None)
    with patch("src.services.scheduler._get_redis", lambda: fake):
        assert _signal_cohort_stats(sess, "BUY", "SWING") == payload
