"""READ-ONLY probe: run the real selection stages against real production prices.

Trains nothing, fits nothing, writes no artifact, mutates no row. It calls `_load_prices`
and `build_features` — both reads — with the ledger's trace attached, so the three selection
stages (required features / available labels / dead-zone selection) and the eligible
opportunity cohort can be inspected against live data before any scheduled retrain runs.

The later stages (dedup, split allocation, embargo, threshold/report) are NOT exercised
here: they belong to a fit. This probe establishes that the instrumentation produces
coherent numbers on real history, not that a whole ledger reconciles.

Usage inside the ml-prediction container:
    python /tmp/probe.py MU GROWTH
"""
import json
import sys

sys.path.insert(0, "/app/src")
sys.path.insert(0, "/app/shared")

from metrics.base_ledger import BaseTrainingLedger, eligible_cohort_for_window  # noqa: E402
from features.builder import build_features, compute_label_threshold  # noqa: E402
# `eligible_cohort_for_window` is imported from the shared module above, NOT from the
# trainer: the trainer binds it inside `train_model`'s body, so it is not a module attribute.
from training.trainer import (  # noqa: E402
    _HORIZON_BY_STYLE, _load_prices, _record_selection_stages, _ts_range,
)

import pandas as pd  # noqa: E402
from datetime import date  # noqa: E402


def probe(symbol: str, style: str) -> dict:
    horizon = _HORIZON_BY_STYLE[style.upper()]
    led = BaseTrainingLedger(subject=symbol, style=style, horizon=horizon, model="probe")

    df = _load_prices(symbol)
    led.record("loaded_bars", rows_in=len(df), rows_out=len(df), date_range=_ts_range(df))

    n_loaded = len(df)
    df = df[pd.to_datetime(df["ts"]).dt.date < date.today()].copy()
    n_dupe = int(pd.to_datetime(df["ts"]).duplicated().sum())
    led.record("completed_unique_bars", rows_in=n_loaded, rows_out=len(df),
               dropped={"bar_dated_today_incomplete": n_loaded - len(df)} if n_loaded != len(df) else {},
               diagnostics={"duplicate_bar_timestamps_present": n_dupe} if n_dupe else {},
               date_range=_ts_range(df))

    # Same dead-zone threshold the trainer fits, on the same training-only slice.
    train_rows = int(len(df) * 0.70)
    threshold = compute_label_threshold(df.iloc[:max(train_rows, 60)], horizon, symbol=symbol)

    trace: dict = {}
    # Macro/sector/fundamental inputs are omitted on purpose: those columns are NaN-allowed,
    # so their absence does not drop rows, and fetching them would make this probe do work
    # the question does not need.
    X, y_dir, _ = build_features(df, horizon=horizon, label_threshold=threshold, trace=trace)
    _record_selection_stages(led, trace, rows_in=len(df))

    out = led.to_dict()
    out["label_threshold"] = threshold
    out["selected_rows"] = int(len(X))
    out["class_support_selected"] = {"pos": int((y_dir == 1).sum()), "neg": int((y_dir == 0).sum())}
    cohort = trace.get("cohort") or {}
    out["cohort_full_series"] = {k: cohort.get(k) for k in
                                 ("n_eligible", "n_selected_for_training",
                                  "n_eligible_excluded_from_training", "date_range",
                                  "excluded_abs_move_max", "class_support_eligible")}
    rows = (cohort.get("rows") or {}).get("date")
    if rows:
        # The last 10% of eligible dates stands in for a test window, so the cohort narrowing
        # is exercised end to end without performing a split.
        out["cohort_tail_window"] = {
            k: v for k, v in eligible_cohort_for_window(
                trace, rows[int(len(rows) * 0.90)], rows[-1]).items() if k != "rows"
        }
    return out


if __name__ == "__main__":
    results = {}
    pairs = [tuple(a.split(":")) for a in sys.argv[1:]] or [("MU", "GROWTH"), ("MU", "LONG"), ("CM", "LONG")]
    for sym, sty in pairs:
        try:
            results[f"{sym}/{sty}"] = probe(sym, sty)
        except Exception as exc:
            results[f"{sym}/{sty}"] = {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(results, indent=2, default=str))
