"""AQ04 — one explicit, evidenced OHLC basis per window. Never a mixture of two.

THE DEFECT. `_adj_close` returned `adj_close.fillna(close)`. Where the adjusted series has
gaps, that splices two different bases into one series and INVENTS MOVEMENT that the market
never made. The audit's witness: a constant raw series [100, 100, 100] with a partial adjusted
series [50, NULL, 50] becomes [50, 100, 50] — a +100% move and a −50% move out of a stock that
did not budge.

A SECOND MIXTURE, in the same place. Indicators that need a range — ATR, Supertrend, the
20-session high/low — took the adjusted CLOSE alongside the RAW high and low. Across any split
those are different scales, so the true range is computed between prices that never coexisted.

THE RULE HERE: a window has ONE basis or it has none.

  * adjusted — every row carries a usable adjustment factor, and high/low/open are restated by
    that same per-row factor so the whole bar moves together.
  * raw — no row carries a factor. Internally consistent, and the dividend effect is simply not
    corrected; that is disclosed rather than patched.
  * PARTIAL coverage falls back to RAW THROUGHOUT, which is a real basis, instead of splicing.
    The adjustment is unavailable for this window, and saying so is the honest answer.

Returning the basis with the series is the point: a caller that cannot say which prices it used
cannot say what its number means, and every consumer of this is measuring returns.
"""
from __future__ import annotations

ADJUSTED = "adjusted"
RAW = "raw"

#: A factor this far from 1.0 is a real corporate action rather than vendor rounding.
FACTOR_TOLERANCE = 1e-6

BASIS_NOTE = {
    ADJUSTED: "Adjusted OHLC: every bar restated by its own adj_close/close factor, so the "
              "whole bar moves together.",
    RAW: "Raw OHLC, internally consistent. No corporate-action adjustment is applied to this "
         "window — dividend and split effects remain in the series.",
}


def resolve_ohlc(df) -> dict:
    """One consistent OHLC basis for this window, with the evidence for the choice.

    `df` is a pandas frame carrying at least `close`, and optionally `adj_close`, `high`,
    `low`, `open`. Returns the series to use plus `basis`, `reason`, and the coverage counts a
    reader needs to judge it.
    """
    close = df["close"].astype(float)
    n = len(close)
    ac = df.get("adj_close")

    usable = 0
    if ac is not None:
        factor = _factor(ac, close)
        usable = int(factor.notna().sum())

    if ac is None or usable == 0:
        return _raw(df, close, covered=0, total=n,
                    reason="no adjusted close is stored for this window")

    if usable < n:
        # PARTIAL. The previous code filled the gaps from `close`, which is the splice that
        # invents movement. Raw throughout is a real basis; a spliced series is not.
        return _raw(df, close, covered=usable, total=n,
                    reason=(f"adjusted close covers {usable} of {n} bars. A window cannot hold "
                            f"two bases, and filling the gaps from the raw close would invent "
                            f"movement the market never made"))

    factor = _factor(ac, close)
    out = {"close": ac.astype(float)}
    for col in ("open", "high", "low"):
        s = df.get(col)
        # THE WHOLE BAR, NOT JUST THE CLOSE. An adjusted close beside a raw high is a true
        # range computed between two prices that never coexisted.
        out[col] = (s.astype(float) * factor) if s is not None else None
    return {**out, "basis": ADJUSTED, "reason": BASIS_NOTE[ADJUSTED],
            "adjusted_bars": usable, "total_bars": n, "adjustment_applied": True,
            "factor_varies": bool(_varies(factor))}


def _raw(df, close, *, covered: int, total: int, reason: str) -> dict:
    return {"close": close,
            "open": df["open"].astype(float) if df.get("open") is not None else None,
            "high": df["high"].astype(float) if df.get("high") is not None else None,
            "low": df["low"].astype(float) if df.get("low") is not None else None,
            "basis": RAW, "reason": f"{reason}. {BASIS_NOTE[RAW]}",
            "adjusted_bars": covered, "total_bars": total, "adjustment_applied": False,
            "factor_varies": None}


def _factor(ac, close):
    """Per-row adj_close/close, NaN where either side is unusable."""
    import numpy as np
    f = ac.astype(float) / close.replace(0, float("nan"))
    return f.replace([float("inf"), -float("inf")], float("nan")).where(np.isfinite(f))


def _varies(factor) -> bool:
    """Did a corporate action occur inside this window? Reported, never used to void it."""
    vals = factor.dropna()
    if len(vals) < 2:
        return False
    return bool((vals.max() - vals.min()) > FACTOR_TOLERANCE * max(abs(vals.max()), 1.0))


def series(df):
    """The close series only, for callers that genuinely need one series.

    Deliberately still returns the BASIS beside it — `_adj_close` returned a bare series, which
    is why two services consumed a spliced one for a year without anywhere to notice.
    """
    r = resolve_ohlc(df)
    return r["close"], r["basis"]
