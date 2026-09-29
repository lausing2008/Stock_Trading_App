"""T411-IVHV: realized-volatility math for the IV vs HV panel.

Every assertion here is a NUMBER the function must produce or a structural property of the
series, computed independently in the test — not a restatement of the implementation. The whole
value of this panel is that the HV line can be trusted next to a real market IV quote; an HV
that is quietly 1.25x too high turns "options are expensive" into "options are cheap" for the
reader with no visible symptom at all.
"""
import importlib.util
import math
import pathlib

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "volatility.py"
_spec = importlib.util.spec_from_file_location("t411_volatility", _SRC)
vol = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vol)

from datetime import date, timedelta


def _dates(n):
    return [date(2026, 1, 1) + timedelta(days=i) for i in range(n)]


def test_annualized_vol_matches_an_independently_computed_figure():
    """A hand-built sample whose standard deviation is known in closed form."""
    # Ten returns alternating +0.01 / -0.01. Mean = 0, every deviation is 0.01, so the sample
    # variance is (10 * 0.0001) / 9 and the annualized figure follows directly.
    rets = [0.01, -0.01] * 5
    expected = math.sqrt((10 * 0.0001) / 9) * math.sqrt(252)
    assert vol.annualized_vol(rets) == expected


def test_a_sample_below_the_floor_is_None_not_zero():
    """A zero would plot as a real reading of 'this stock did not move'. That is a different
    claim from 'there is not enough history to say', and the chart must not conflate them."""
    assert vol.annualized_vol([0.01] * (vol.MIN_HV_SAMPLE - 1)) is None
    assert vol.annualized_vol([]) is None


def test_a_genuinely_flat_series_is_a_real_zero():
    """The mirror of the test above: identical returns every day IS a measurement of zero
    dispersion, and must not be suppressed as though it were missing data."""
    assert vol.annualized_vol([0.005] * 20) == 0.0


def test_log_returns_are_used_not_simple_returns():
    """Behavioural, not a source read: the two conventions give measurably different numbers,
    and only the log one belongs next to a Black-Scholes implied volatility.

    A doubling and a halving give simple returns of +1.0 and -0.5 but log returns of +ln2 and
    -ln2 — so a log-return series of an alternating double/halve is perfectly symmetric (zero
    mean) while the simple-return one is not. Asserting on the resulting volatility pins the
    convention without naming any function the implementation calls.
    """
    closes = [100.0, 200.0, 100.0, 200.0, 100.0, 200.0, 100.0, 200.0, 100.0, 200.0, 100.0]
    rets = vol.log_returns(closes)
    assert len(rets) == 10
    assert abs(sum(rets)) < 1e-12, "log returns of a perfect round trip must cancel to zero"
    # Under simple returns the same series would have a non-zero mean (+0.25).
    simple = [(b / a) - 1 for a, b in zip(closes, closes[1:])]
    assert abs(sum(simple) / len(simple) - 0.25) < 1e-12


def test_log_returns_skip_non_positive_closes_without_killing_the_series():
    """A single bad tick must not take out the whole line, and must never silently produce a
    number — log() is undefined at or below zero."""
    out = vol.log_returns([100.0, 0.0, 100.0, 101.0])
    assert len(out) == 1
    assert out[0] == math.log(101.0 / 100.0)


def test_series_emits_no_point_until_a_full_window_exists():
    """A 3-day HV plotted beside a 20-day HV is not the same measurement. The early dates must
    produce NOTHING rather than a partial-window point."""
    n = 30
    bars = list(zip(_dates(n), [100.0 + i for i in range(n)]))
    series = vol.historical_volatility_series(bars, window=20)
    assert len(series) == n - 20
    first_date = series[0][0]
    assert first_date == bars[20][0], "the first point must sit at the 21st bar, not earlier"


def test_series_is_empty_when_history_is_shorter_than_the_window():
    bars = list(zip(_dates(10), [100.0 + i for i in range(10)]))
    assert vol.historical_volatility_series(bars, window=20) == []


def test_series_values_match_the_rolling_window_computed_independently():
    """Recompute one interior point from scratch and require an exact match — this is what
    catches an off-by-one in the window slice, which would shift the whole line sideways."""
    closes = [100.0, 102.0, 99.0, 103.0, 101.0, 105.0, 104.0, 99.0, 98.0, 103.0,
              107.0, 104.0, 102.0, 108.0, 110.0, 107.0, 105.0, 111.0, 109.0, 113.0,
              112.0, 108.0, 115.0, 117.0, 114.0]
    bars = list(zip(_dates(len(closes)), closes))
    window = 20
    series = vol.historical_volatility_series(bars, window=window)
    assert series

    d, v = series[0]
    i = closes.index(closes[window])  # the index the first point must correspond to
    expected_rets = [math.log(b / a) for a, b in zip(closes[i - window:i + 1], closes[i - window + 1:i + 1])]
    assert len(expected_rets) == window
    mean = sum(expected_rets) / window
    var = sum((r - mean) ** 2 for r in expected_rets) / (window - 1)
    assert abs(v - math.sqrt(var) * math.sqrt(252)) < 1e-12
    assert d == bars[i][0]


def test_window_below_two_is_rejected_rather_than_producing_nonsense():
    try:
        vol.historical_volatility_series([], window=1)
    except ValueError:
        return
    raise AssertionError("a window of 1 has no returns in it and must not be accepted")


def test_output_is_a_fraction_not_a_percent():
    """Units matter more than usual here: IV arrives from Unusual Whales as a fraction (0.247),
    and the two series share one chart axis. A percent here would render HV 100x too high."""
    n = 40
    # ~1% daily moves -> annualized vol in the neighbourhood of 0.16, nowhere near 16.
    closes = [100.0 * (1.01 if i % 2 else 0.99) ** 1 for i in range(n)]
    running = [100.0]
    for i in range(1, n):
        running.append(running[-1] * (1.01 if i % 2 else 0.99))
    series = vol.historical_volatility_series(list(zip(_dates(n), running)))
    assert series
    for _, v in series:
        assert 0.0 <= v < 1.5, f"{v} does not look like an annualized fraction"
