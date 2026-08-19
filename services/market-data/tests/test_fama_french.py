"""Tests for IF-07: fama_french.py — real Fama-French 5-factor OLS regression of a paper
portfolio's own daily equity-curve returns, genuinely distinct from the two pre-existing
"factor" endpoints in this app (signal-engine's factor-exposure/factor_attribution, both
signal-QUALITY diagnostics comparing technical-indicator values, never a real factor-return
regression).

fetch_ff5_factors() and compute_factor_exposure() are both pure (no DB/network dependency —
the actual HTTP GET lives in the separate fetch_ff5_factors_live() wrapper, deliberately kept
thin so the parser itself stays directly testable). numpy is a real, installed package in this
test environment (per conftest.py's own docstring for sibling modules); httpx is stubbed, but
neither tested function here touches it.

fetch_ff5_factors() is tested against a REAL, hand-constructed-but-format-faithful fixture
matching Ken French's own actual CSV format exactly (header rows + fixed-width comma-separated
data, verified directly against a real downloaded copy of the file during this feature's own
development — not a hand-idealized sample, matching this repo's own established "test parsers
against real captured data" discipline documented for the CAPE/multpl.com parser's own
en-space-entity bug).
"""
import zipfile
import io
from datetime import date

from src.services.fama_french import fetch_ff5_factors, compute_factor_exposure, _FACTOR_NAMES


def _make_ff5_zip(csv_body: str) -> bytes:
    """Build a real in-memory zip matching Ken French's own distribution format (a single CSV
    inside a zip), so fetch_ff5_factors()'s real zipfile-extraction logic is exercised too."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("F-F_Research_Data_5_Factors_2x3_daily.csv", csv_body)
    return buf.getvalue()


# A real, format-faithful excerpt — header prose lines + the exact comma/whitespace pattern
# Ken French's own file uses, verified directly against a real downloaded copy during
# development (see the source module's own docstring for the verification method).
_REAL_FORMAT_CSV = """This file was created by using the 202606 CRSP database.
The Tbill return is the simple daily rate that, over the number of trading days
compounds to 1-month TBill rate.

,Mkt-RF,SMB,HML,RMW,CMA,RF
20260623,   -1.31,    0.97,    0.50,    1.24,    0.31,    0.01
20260624,   -0.07,    0.78,   -0.21,    0.42,    0.61,    0.01
20260625,   -0.13,    0.61,    0.91,   -0.76,    0.51,    0.01
20260626,    0.15,    1.36,   -0.95,    0.44,    0.04,    0.01
20260629,    1.20,   -0.89,   -0.90,   -1.77,   -0.50,    0.01

Copyright 2026 Eugene F. Fama and Kenneth R. French
"""


def test_parses_the_real_ken_french_csv_format_correctly():
    zip_bytes = _make_ff5_zip(_REAL_FORMAT_CSV)
    result = fetch_ff5_factors(zip_bytes)
    assert len(result) == 5
    assert "2026-06-23" in result
    row = result["2026-06-23"]
    assert row["Mkt-RF"] == -1.31
    assert row["SMB"] == 0.97
    assert row["HML"] == 0.50
    assert row["RMW"] == 1.24
    assert row["CMA"] == 0.31
    assert row["RF"] == 0.01


def test_header_and_footer_prose_lines_are_silently_skipped_not_crashed_on():
    zip_bytes = _make_ff5_zip(_REAL_FORMAT_CSV)
    result = fetch_ff5_factors(zip_bytes)
    # exactly the 5 real data rows, no phantom entries from the header/footer prose
    assert len(result) == 5


def test_empty_zip_with_no_csv_returns_empty_dict_not_a_crash():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "not a csv")
    result = fetch_ff5_factors(buf.getvalue())
    assert result == {}


def test_malformed_csv_content_is_skipped_line_by_line_not_a_hard_failure():
    csv_with_garbage = _REAL_FORMAT_CSV.replace("20260624,   -0.07,", "GARBAGE_LINE,   -0.07,")
    result = fetch_ff5_factors(_make_ff5_zip(csv_with_garbage))
    assert "2026-06-24" not in result
    assert len(result) == 4  # the other 4 real rows still parse fine


def test_the_date_field_must_be_exactly_8_digits_not_any_non_whitespace_token():
    """Distinguishes the regex's OWN date-shape requirement from the separate try/except
    ValueError guard around datetime.strptime() — a non-numeric-but-comma-separated line
    (like the GARBAGE_LINE case above) gets caught by the strptime guard either way, which
    would let a looser \\S+ pattern silently pass this same test for the wrong reason. This
    uses a line with the WRONG NUMBER OF DIGITS (7, not 8) — a shape the regex itself must
    reject, since strptime("2026062", "%Y%m%d") would also raise, but only checking that
    outcome wouldn't prove the regex enforces exactly 8 digits specifically."""
    csv_with_short_date = _REAL_FORMAT_CSV.replace(
        "20260624,   -0.07,    0.78,   -0.21,    0.42,    0.61,    0.01",
        "2026062,   -0.07,    0.78,   -0.21,    0.42,    0.61,    0.01",
    )
    result = fetch_ff5_factors(_make_ff5_zip(csv_with_short_date))
    assert len(result) == 4  # the malformed 7-digit-date row must be excluded
    assert all(len(d) == 10 for d in result.keys())  # every real key is a genuine ISO date


# ── compute_factor_exposure() ────────────────────────────────────────────────────────────────

def _synthetic_factors(n_days: int, start_year=2026, start_month=1, start_day=1) -> dict:
    """A real, non-degenerate synthetic factor history — deliberately varied per day (not a
    flat repeat) so the regression has real explanatory variance to fit against."""
    from datetime import timedelta
    factors = {}
    d = date(start_year, start_month, start_day)
    for i in range(n_days):
        factors[d.isoformat()] = {
            "Mkt-RF": 0.5 + (i % 5) * 0.1 - 0.2,
            "SMB": 0.1 * ((i % 3) - 1),
            "HML": 0.05 * ((i % 4) - 2),
            "RMW": 0.02 * ((i % 2) - 0.5),
            "CMA": 0.03 * ((i % 6) - 3),
            "RF": 0.01,
        }
        d += timedelta(days=1)
    return factors


def test_returns_insufficient_data_below_the_min_regression_days_floor():
    factors = _synthetic_factors(10)
    days = sorted(factors.keys())
    equity_curve = [(date.fromisoformat(d), 100.0 * (1.001 ** i)) for i, d in enumerate(days)]
    result = compute_factor_exposure(equity_curve, factors)
    assert result["insufficient_data"] is True
    assert result["alpha_daily_pct"] is None
    assert all(v is None for v in result["betas"].values())


def test_a_pure_beta_one_market_tracker_recovers_beta_one_and_zero_alpha():
    """Hand-verified property: a portfolio whose daily return EXACTLY equals Mkt-RF + RF (pure
    market beta=1, zero skill/alpha, zero exposure to the other 4 factors) must regress to
    beta_MktRF~=1.0, all other betas~=0.0, alpha~=0.0, R^2~=1.0 — the simplest possible
    correctness check for the whole regression."""
    factors = _synthetic_factors(40)
    days = sorted(factors.keys())
    equity_curve = []
    eq = 100.0
    for d in days:
        f = factors[d]
        ret_pct = f["Mkt-RF"] + f["RF"]
        eq = eq * (1 + ret_pct / 100)
        equity_curve.append((date.fromisoformat(d), eq))

    result = compute_factor_exposure(equity_curve, factors)
    assert result["insufficient_data"] is False
    assert abs(result["betas"]["Mkt-RF"] - 1.0) < 0.01
    for name in ("SMB", "HML", "RMW", "CMA"):
        assert abs(result["betas"][name]) < 0.01
    assert abs(result["alpha_daily_pct"]) < 0.01
    assert result["r_squared"] > 0.99


def test_dates_with_no_matching_factor_row_are_silently_excluded_from_the_regression():
    """An equity-curve date with no corresponding factor row (e.g. a market holiday the
    portfolio's own curve still recorded, or a gap in the factor file) must be skipped, never
    crash the regression and never be silently treated as a zero-factor day."""
    factors = _synthetic_factors(30)
    days = sorted(factors.keys())
    equity_curve = [(date.fromisoformat(d), 100.0 * (1.001 ** i)) for i, d in enumerate(days)]
    # add 5 extra equity-curve days with NO matching factor data at all
    from datetime import timedelta
    last_date = date.fromisoformat(days[-1])
    for i in range(1, 6):
        equity_curve.append((last_date + timedelta(days=i), equity_curve[-1][1] * 1.001))

    result = compute_factor_exposure(equity_curve, factors)
    # sample_size must reflect only the ALIGNED rows (30 days of returns from 30 equity
    # points minus the day-1 anchor = 29 possible returns, all of which DO have factor data;
    # the 5 extra no-factor days must be excluded, not silently padded in)
    assert result["sample_size"] == 29


def test_report_includes_r_squared_and_sample_size_alongside_every_estimate():
    """The module's own stated caveat (never present a noisy alpha without reliability
    context) must be genuinely reflected in the response shape — r_squared and sample_size
    must always be present alongside alpha/betas, not just on request."""
    factors = _synthetic_factors(40)
    days = sorted(factors.keys())
    equity_curve = [(date.fromisoformat(d), 100.0 * (1.0005 ** i)) for i, d in enumerate(days)]
    result = compute_factor_exposure(equity_curve, factors)
    assert "r_squared" in result
    assert "sample_size" in result
    assert result["r_squared"] is not None
    assert set(result["betas"].keys()) == set(_FACTOR_NAMES)


def test_zero_or_one_equity_points_returns_the_empty_shape_not_a_crash():
    assert compute_factor_exposure([], {})["insufficient_data"] is True
    assert compute_factor_exposure([(date(2026, 1, 1), 100.0)], {})["insufficient_data"] is True


# ── GET /paper-portfolio/fama-french — source-text regression checks ───────────────────────
# paper_portfolio.py can't be imported directly in this test environment (its import chain
# needs the real conftest.py stub setup only pytest's own collection provides for `db`/
# `db.models`) — the route's wiring is covered by source-text checks, matching this repo's
# established pattern for functions in this exact file (e.g. check_portfolio_drawdown_alerts()
# in scheduler.py, covered the same way in test_drawdown_alert.py).

import pathlib as _pathlib

_PAPER_PORTFOLIO_PATH = _pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "paper_portfolio.py"
_PAPER_PORTFOLIO_SOURCE = _PAPER_PORTFOLIO_PATH.read_text()


def _route_body(func_name: str) -> str:
    start = _PAPER_PORTFOLIO_SOURCE.index(f"def {func_name}(")
    end = _PAPER_PORTFOLIO_SOURCE.index("\ndef ", start + 1)
    return _PAPER_PORTFOLIO_SOURCE[start:end]


def test_the_route_is_registered_at_the_documented_path():
    assert '@router.get("/fama-french")' in _PAPER_PORTFOLIO_SOURCE


def test_the_response_includes_the_reliability_caveat_directly_not_just_in_a_code_comment():
    body = _route_body("get_fama_french_exposure")
    assert '"note":' in body
    assert "confidence intervals" in body.lower()


def test_the_route_reuses_compute_factor_exposure_not_a_second_derivation():
    body = _route_body("get_fama_french_exposure")
    assert "from ..services.fama_french import compute_factor_exposure" in body
    assert "compute_factor_exposure(equity_curve, factors)" in body


def test_the_ff5_cache_fails_open_to_a_live_fetch_on_any_redis_error():
    body = _route_body("_get_cached_ff5_factors")
    assert "except Exception:" in body
    assert "fetch_ff5_factors_live()" in body


def test_missing_factor_data_returns_an_explicit_error_not_a_silent_empty_result():
    body = _route_body("get_fama_french_exposure")
    assert "if not factors:" in body
    assert '"error":' in body
