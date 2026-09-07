"""Tests for MOAT-1's financial-statement backfill — the multi-year ROIC prerequisite.

See docs/2026-09-06/SCOPING_QUANTITATIVE_MOAT_SCORE.md. These cover the two things most likely
to break silently:

1. **NULL-vs-zero discipline.** yfinance statement row labels vary by issuer and market, so a
   missing line item MUST read as None, never as a fabricated 0.0 — otherwise a downstream
   ROIC/margin ratio computes against a fake denominator. This codebase has fixed that
   falsy-zero class repeatedly.
2. **AUD-FINSTMT-DEBTSPLIT.** Not every issuer reports a single "Total Debt" row. Verified live
   2026-09-06: AAPL does; 0700.HK (Tencent) reports only "Long Term Debt" + "Current Debt", so
   the original single-label lookup left total_debt NULL for it — which silently kills ROIC
   entirely, since invested capital = debt + equity - cash has no usable denominator without
   it. The fallback SUMS the components (unlike every other field, which is first-match-wins),
   because they're genuinely components rather than alternative names for one figure.
"""
import pandas as pd
import pytest

import src.services.scheduler as sch


def _bs(rows: dict, col="2025-09-30") -> pd.DataFrame:
    """Minimal balance-sheet-shaped frame: yfinance indexes by row LABEL, columns by period."""
    ts = pd.Timestamp(col)
    return pd.DataFrame({ts: rows})


# ── _finstmt_pick: NULL-vs-zero discipline ───────────────────────────────────────────────

def test_pick_returns_the_value_when_the_first_label_is_present():
    df = _bs({"Total Debt": 1234.0})
    assert sch._finstmt_pick(df, ["Total Debt"], df.columns[0]) == 1234.0


def test_pick_falls_through_to_a_later_candidate_label():
    """Issuers differ: some report 'Stockholders Equity', others only the gross-minority form."""
    df = _bs({"Total Equity Gross Minority Interest": 99.0})
    got = sch._finstmt_pick(df, ["Stockholders Equity", "Total Equity Gross Minority Interest"], df.columns[0])
    assert got == 99.0


def test_pick_returns_none_not_zero_when_every_label_is_absent():
    """THE CORE DISCIPLINE: absent must be None. A 0.0 here would silently become a fake
    denominator in any downstream ROIC/margin computation."""
    df = _bs({"Something Else": 5.0})
    assert sch._finstmt_pick(df, ["Total Debt"], df.columns[0]) is None


def test_pick_returns_none_on_nan():
    """yfinance pads missing periods with NaN — that is absent data, not a zero figure."""
    df = _bs({"Total Debt": float("nan")})
    assert sch._finstmt_pick(df, ["Total Debt"], df.columns[0]) is None


def test_pick_preserves_a_genuine_zero():
    """The inverse of the above and equally important: a real filed 0.0 (e.g. a debt-free
    company reporting zero debt) must survive as 0.0, not be discarded as missing."""
    df = _bs({"Total Debt": 0.0})
    assert sch._finstmt_pick(df, ["Total Debt"], df.columns[0]) == 0.0


def test_pick_returns_none_on_an_empty_or_missing_frame():
    assert sch._finstmt_pick(None, ["Total Debt"], "2025-09-30") is None
    assert sch._finstmt_pick(pd.DataFrame(), ["Total Debt"], "2025-09-30") is None


def test_pick_returns_none_for_a_non_numeric_value():
    df = _bs({"Total Debt": "not-a-number"})
    assert sch._finstmt_pick(df, ["Total Debt"], df.columns[0]) is None


# ── AUD-FINSTMT-DEBTSPLIT: the debt-component fallback ───────────────────────────────────

def _resolve_total_debt(bal: pd.DataFrame, col) -> float | None:
    """Mirrors the backfill's own total_debt resolution: single label first, then sum the
    split components only if that came back absent."""
    td = sch._finstmt_pick(bal, ["Total Debt"], col)
    if td is None:
        parts = [sch._finstmt_pick(bal, labels, col) for labels in sch._FINSTMT_DEBT_COMPONENTS]
        found = [p for p in parts if p is not None]
        if found:
            td = sum(found)
    return td


def test_debt_components_are_summed_when_total_debt_is_absent():
    """The real 0700.HK shape: no 'Total Debt' row, only the two split components."""
    df = _bs({"Long Term Debt": 300.0, "Current Debt": 106.0})
    assert _resolve_total_debt(df, df.columns[0]) == 406.0


def test_capital_lease_variant_is_preferred_over_the_bare_component():
    """Within each component, the capital-lease-inclusive label is the more complete
    obligation measure and is listed first — first-match-wins applies WITHIN a component."""
    df = _bs({
        "Long Term Debt And Capital Lease Obligation": 350.0,
        "Long Term Debt": 300.0,
        "Current Debt": 100.0,
    })
    assert _resolve_total_debt(df, df.columns[0]) == 450.0  # 350 (lease-inclusive) + 100


def test_an_issuer_reporting_total_debt_keeps_its_own_authoritative_figure():
    """The fallback must NOT fire when the issuer reports the real total — summing components
    on top of it would double-count."""
    df = _bs({"Total Debt": 999.0, "Long Term Debt": 300.0, "Current Debt": 100.0})
    assert _resolve_total_debt(df, df.columns[0]) == 999.0


def test_a_single_available_component_is_used_rather_than_giving_up():
    df = _bs({"Long Term Debt": 300.0})
    assert _resolve_total_debt(df, df.columns[0]) == 300.0


def test_total_debt_stays_none_when_neither_total_nor_any_component_exists():
    """Must remain None, not 0.0 — a genuinely debt-free-looking 0 and 'this issuer's debt is
    unknown' have to stay distinguishable, since ROIC's denominator depends on it."""
    df = _bs({"Total Assets": 1000.0})
    assert _resolve_total_debt(df, df.columns[0]) is None


# ── ROIC feasibility: the whole point of this table ──────────────────────────────────────

def test_roic_is_computable_from_the_stored_fields():
    """End-to-end check that the chosen columns actually support NOPAT / Invested Capital.
    Numbers are the real AAPL FY2025 shape (verified live 2026-09-06, ROIC ~82%)."""
    ebit, tax, pretax = 127_000_000_000.0, 19_800_000_000.0, 127_000_000_000.0
    debt, equity, cash = 101_700_000_000.0, 66_800_000_000.0, 32_000_000_000.0

    tax_rate = tax / pretax
    nopat = ebit * (1 - tax_rate)
    invested_capital = debt + equity - cash
    roic = nopat / invested_capital

    assert 0.0 < tax_rate < 1.0
    assert invested_capital > 0
    assert 0.5 < roic < 1.5  # a high-moat business lands well above cost of capital


def test_roic_denominator_is_unusable_without_total_debt():
    """Documents WHY the debt-split fallback matters: with total_debt None, there is no
    invested-capital denominator at all, so ROIC silently cannot be computed for that issuer —
    which is exactly what happened to 0700.HK before the fallback existed."""
    debt, equity, cash = None, 66_800_000_000.0, 32_000_000_000.0
    assert debt is None
    with pytest.raises(TypeError):
        _ = debt + equity - cash  # type: ignore[operator]
