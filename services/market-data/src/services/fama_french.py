"""IF-07: Fama-French 5-factor exposure analysis for a real paper-portfolio equity curve.

Genuinely distinct from BOTH pre-existing "factor" endpoints in this app (per this tracker
item's own name-collision warning): signal-engine's GET /signals/factor-exposure compares
TECHNICAL INDICATOR values (rsi/adx/volume_z/etc.) between correct and wrong BUY calls, and
GET /signals/factor_attribution computes per-boolean-reason-flag win-rate edge — both are
signal-QUALITY diagnostics, neither regresses a real return series against real Fama-French
factor returns. This module does the latter: a real OLS regression of a portfolio's own daily
equity-curve returns against the 5 Fama-French factors (Mkt-RF, SMB, HML, RMW, CMA), answering
"is my return actually just market beta / a style tilt, or genuine stock selection (alpha)?"

Data source: Ken French's own data library (mba.tuck.dartmouth.edu/pages/faculty/ken.french/
ftp/) — a free, stable, legitimate, citable academic source (unlike several other data ideas
in this doc set that were rejected for ToS/scraping risk). Verified LIVE before building
anything (not just "reachable"): a direct HEAD request confirmed Last-Modified within the
current month, and the CSV's own last row is dated within days of "today" at investigation
time — matching this repo's own standing "reachable != current" discipline.

No statsmodels dependency needed — a 5-factor OLS is solved directly via numpy least-squares
(np.linalg.lstsq), matching this tracker item's own explicit recommendation to avoid a new
heavy dependency for a problem this small.

IMPORTANT, STATED CAVEAT (per this tracker item's own fix note): with only a few months of
daily equity-curve history, the confidence intervals on these betas are very wide. This module
always reports R² and sample_size alongside every beta/alpha estimate specifically so a caller
can judge reliability rather than trusting a point estimate at face value — never presents a
noisy alpha as a confirmed finding.
"""
import io
import re
import zipfile
from datetime import date, datetime

import httpx
import numpy as np

from common.logging import get_logger

log = get_logger("fama_french")

_FF5_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_5_Factors_2x3_daily_CSV.zip"
_FF5_CSV_NAME = "F-F_Research_Data_5_Factors_2x3_daily.csv"
_FACTOR_NAMES = ["Mkt-RF", "SMB", "HML", "RMW", "CMA"]

# _MIN_REGRESSION_DAYS: the same 20-day annualizing-sample floor _portfolio_risk_metrics()
# already uses for Sharpe/Sortino/CAGR (paper_portfolio.py's own _MIN_SHARPE_DAYS) — a 5-factor
# OLS with fewer degrees of freedom than this is not a trustworthy regression at all.
_MIN_REGRESSION_DAYS = 20


def fetch_ff5_factors(raw_zip_bytes: bytes) -> dict[str, dict[str, float]]:
    """Parse Ken French's 5-factor daily CSV (already downloaded as raw zip bytes) into
    {date_iso: {"Mkt-RF": pct, "SMB": pct, ...}}. Values are PERCENTAGES (e.g. -0.67 for -0.67%),
    matching the raw file's own units — callers must divide by 100 before combining with a
    fractional equity-curve return.

    Pure parsing logic, no network call inside this function — the actual HTTP GET is a
    separate, thin caller so this function stays directly unit-testable against a real,
    captured fixture file (matching this repo's own established "test parsers against REAL
    captured data, not a hand-idealized sample" discipline — see the CAPE/multpl.com parser's
    own documented en-space-entity bug for exactly why this matters).
    """
    with zipfile.ZipFile(io.BytesIO(raw_zip_bytes)) as zf:
        names = [n for n in zf.namelist() if n.endswith(".csv")]
        if not names:
            return {}
        with zf.open(names[0]) as f:
            text = f.read().decode("utf-8", errors="replace")

    result: dict[str, dict[str, float]] = {}
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"^(\d{8}),\s*([-\d.]+),\s*([-\d.]+),\s*([-\d.]+),\s*([-\d.]+),\s*([-\d.]+),\s*([-\d.]+)\s*$", line)
        if not m:
            continue
        try:
            d = datetime.strptime(m.group(1), "%Y%m%d").date().isoformat()
            result[d] = {
                "Mkt-RF": float(m.group(2)),
                "SMB": float(m.group(3)),
                "HML": float(m.group(4)),
                "RMW": float(m.group(5)),
                "CMA": float(m.group(6)),
                "RF": float(m.group(7)),
            }
        except ValueError:
            continue
    return result


def fetch_ff5_factors_live(timeout: int = 30) -> dict[str, dict[str, float]]:
    """Real network fetch — a thin wrapper around fetch_ff5_factors() so the parser itself
    stays testable with no network dependency."""
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.get(_FF5_URL, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            return fetch_ff5_factors(r.content)
    except Exception as exc:
        log.error("fama_french.fetch_failed", error=str(exc))
        return {}


def compute_factor_exposure(equity_curve: list[tuple[date, float]], factors: dict[str, dict[str, float]]) -> dict:
    """Regress a portfolio's own daily equity-curve returns on the 5 Fama-French factors via
    OLS (numpy least-squares — no statsmodels dependency needed for a regression this small).

    equity_curve: chronologically-ordered [(date, equity), ...] — the SAME shape
    _portfolio_risk_metrics() already consumes from PaperEquityCurve rows.
    factors: {date_iso: {"Mkt-RF": pct, ...}} as returned by fetch_ff5_factors().

    Returns None values (never a fabricated number) below _MIN_REGRESSION_DAYS aligned
    observations — a regression on a handful of days is not a trustworthy estimate.

    The regression itself: excess_portfolio_return = alpha + b1*MktRF + b2*SMB + b3*HML +
    b4*RMW + b5*CMA + residual. alpha and each beta are reported alongside r_squared and
    sample_size so a caller can judge reliability directly, never presenting a noisy
    estimate as a confirmed finding on its own (per this module's own stated caveat).
    """
    if len(equity_curve) < 2:
        return _empty_result(0)

    daily_returns = []
    for i in range(1, len(equity_curve)):
        prev_date, prev_eq = equity_curve[i - 1]
        cur_date, cur_eq = equity_curve[i]
        if prev_eq and prev_eq > 0:
            daily_returns.append((cur_date, (cur_eq / prev_eq - 1) * 100))  # as a percentage, matching factor units

    aligned_y = []
    aligned_x = []
    for d, ret in daily_returns:
        f = factors.get(d.isoformat())
        if f is None:
            continue
        excess_ret = ret - f["RF"]
        aligned_y.append(excess_ret)
        aligned_x.append([f[name] for name in _FACTOR_NAMES])

    n = len(aligned_y)
    if n < _MIN_REGRESSION_DAYS:
        return _empty_result(n)

    y = np.array(aligned_y, dtype=float)
    X = np.array(aligned_x, dtype=float)
    X_design = np.column_stack([np.ones(n), X])  # intercept (alpha) + 5 factor betas

    coeffs, _, _, _ = np.linalg.lstsq(X_design, y, rcond=None)
    alpha = coeffs[0]
    betas = coeffs[1:]

    y_pred = X_design @ coeffs
    ss_res = float(np.sum((y - y_pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

    return {
        "sample_size": n,
        "insufficient_data": False,
        "alpha_daily_pct": round(float(alpha), 4),
        "alpha_annualized_pct": round(float(alpha) * 252, 2),
        "r_squared": round(r_squared, 4),
        "betas": {name: round(float(b), 3) for name, b in zip(_FACTOR_NAMES, betas)},
    }


def _empty_result(n: int) -> dict:
    return {
        "sample_size": n,
        "insufficient_data": True,
        "alpha_daily_pct": None,
        "alpha_annualized_pct": None,
        "r_squared": None,
        "betas": {name: None for name in _FACTOR_NAMES},
    }
