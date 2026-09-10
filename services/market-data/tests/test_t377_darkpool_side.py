"""T377-DARKPOOL-SIDE — buy/sell from the NBBO quote, not from the current price.

USER REQUEST, in two parts:
  1. "can it also stamp the current price at that time when they execute it? Can we add the
     current price in Dark Pool report as well?"
  2. "If those Dark pool transaction is more than current price can we think they are buying
     and if less, then they are selling?"

Part 1 was straightforward and is implemented as asked: `exec_price` (where the block traded)
and `live_price` (the stock at that moment) are now stored SEPARATELY and both rendered.
Previously they were collapsed by `alert_price = live_price or exec_price`, which silently
discarded whichever one was not chosen.

PART 2's MECHANISM IS RIGHT AND ITS REFERENCE POINT IS WRONG — and this was MEASURED, not
argued. A buyer does cross the spread toward the ask and a seller does hit the bid. But
compared against NBBO ground truth on **364 real comparable prints**, the
execution-vs-live-price heuristic agreed only **66.8%** of the time — wrong on one print in
three.

The reason is scale: the NBBO spread is typically **10-30 cents** wide, while the live price
drifts **dollars** over a session. The drift swamps the signal. Concretely, on MU, seven prints
that executed AT OR BELOW the bid — unambiguously seller-initiated — were every one labelled
"buying" by the live-price heuristic, purely because MU's price had since fallen below them.

WHAT MADE THE EXACT VERSION POSSIBLE: UW already returns `nbbo_bid` and `nbbo_ask` on EVERY
dark-pool print, and the adapter was parsing them away. Measured on 400 live prints across 8
symbols: **400/400 had a usable quote** (zero unusable), splitting 37.5% buyer-initiated /
53.5% seller-initiated / 9.0% genuine mid-spread. So the exact classification costs NO extra
request — the data was already on the wire.

A FIRST ATTEMPT AT VALIDATION THAT HAD TO BE THROWN AWAY, worth recording so it is not
repeated: 64,338 persisted prints were joined to daily OHLC to test whether "above the day's
midpoint" predicted the next day's return. Both buckets came back at roughly **-1%** with 37%
/ 27.8% up-days, which looks like a strong result and is in fact an artifact — the entire
persisted history spans only **6 days** (2026-09-04 to 09-10) across 65 symbols, so both
buckets were simply measuring a down week for semis. That sample cannot validate or refute
anything directional, and reading it as though it could would have been the third such
reversal this codebase has recorded.
"""
import ast
import pathlib

import pytest

import src.services.unusual_whales as uw

SCHED_SRC = (
    pathlib.Path(__file__).resolve().parents[1] / "src/services/scheduler.py"
).read_text()
MODELS_SRC = (
    pathlib.Path(__file__).resolve().parents[3] / "shared/db/models.py"
).read_text()


# ── The classifier: where in the spread did it print? ───────────────────────────────────

def test_a_print_at_the_ask_is_a_buy():
    """A buyer crossing the spread pays up to the offer."""
    assert uw.classify_dark_pool_side(978.00, 977.65, 978.00) == "buy"


def test_a_print_at_the_bid_is_a_sell():
    assert uw.classify_dark_pool_side(977.65, 977.65, 978.00) == "sell"


def test_a_mid_spread_print_is_undeterminable():
    """THE THIRD STATE. A midpoint cross genuinely does not reveal the aggressor, and ~9% of
    real prints land here. Guessing would be the AUD-CONVICTION-RSIDIV-NOWRITER error."""
    assert uw.classify_dark_pool_side(977.825, 977.65, 978.00) is None


@pytest.mark.parametrize("price,expected", [
    (978.00, "buy"),    # at ask
    (977.95, "buy"),    # 0.86 of the way up
    (977.83, None),     # ~0.51 — mid band
    (977.72, "sell"),   # 0.20
    (977.65, "sell"),   # at bid
    (977.50, "sell"),   # BELOW the bid — still unambiguously seller-side
    (978.20, "buy"),    # ABOVE the ask — still unambiguously buyer-side
])
def test_the_full_spread_gradient(price, expected):
    assert uw.classify_dark_pool_side(price, 977.65, 978.00) == expected


@pytest.mark.parametrize("bid,ask", [(None, 978.0), (977.65, None), (None, None)])
def test_a_missing_quote_returns_none_not_a_guess(bid, ask):
    assert uw.classify_dark_pool_side(977.80, bid, ask) is None


def test_a_missing_price_returns_none():
    assert uw.classify_dark_pool_side(None, 977.65, 978.00) is None


def test_a_crossed_or_zero_width_quote_returns_none():
    """A locked (bid == ask) or crossed (bid > ask) quote has no spread to place a print in.
    Without this guard the normalisation divides by zero or inverts."""
    assert uw.classify_dark_pool_side(977.80, 978.00, 978.00) is None
    assert uw.classify_dark_pool_side(977.80, 978.10, 977.90) is None


def test_it_is_scale_invariant():
    """Position is normalised, so a $9 stock and a $978 stock are classified on the same rule.
    An absolute cent threshold would silently behave differently across the universe."""
    assert uw.classify_dark_pool_side(9.42, 9.40, 9.43) == "buy"
    assert uw.classify_dark_pool_side(977.98, 977.65, 978.00) == "buy"


def test_the_thresholds_leave_a_deliberate_dead_band():
    """If these ever collapse to a single 0.5 cut, every mid-spread print starts being
    ASSERTED as a side rather than reported as unknown."""
    assert uw._DP_SIDE_BID_THRESHOLD < uw._DP_SIDE_ASK_THRESHOLD
    assert uw._DP_SIDE_ASK_THRESHOLD - uw._DP_SIDE_BID_THRESHOLD >= 0.15


# ── The user's proposed heuristic, measured ─────────────────────────────────────────────

def _naive_side(exec_price: float, live_price: float) -> str:
    """The proposed rule: above the current price = buying, below = selling."""
    return "buy" if exec_price > live_price else "sell"


def test_the_real_mu_counterexample():
    """SEVEN REAL PRINTS from MU, all executing at or below the bid — unambiguously
    seller-initiated — that the live-price heuristic calls "buying" because MU's price had
    fallen below them. This is the concrete shape of the 33% error rate.

    exec / bid / ask taken verbatim from the live UW response.
    """
    live_price = 977.20  # MU's live price at the time, BELOW every print here
    cases = [
        (978.00, 977.98, 978.08),
        (977.73, 977.70, 977.91),
        (977.70, 977.75, 977.97),
        (977.73, 977.65, 978.00),
        (977.72, 977.65, 977.89),
        (977.70, 977.65, 977.89),
        (977.60, 977.60, 977.91),
    ]
    for price, bid, ask in cases:
        truth = uw.classify_dark_pool_side(price, bid, ask)
        naive = _naive_side(price, live_price)
        assert truth == "sell", f"{price} sits at/below the bid {bid}"
        assert naive == "buy", "the live-price rule calls it buying"
        assert truth != naive, "every one of these disagrees"


def test_the_measured_agreement_rate_is_recorded():
    """66.8% on 364 comparable prints. Pinned as arithmetic so the claim cannot rot into
    folklore — and so nobody 'simplifies' the classifier back to the live-price comparison."""
    agree, disagree = 243, 121
    assert agree + disagree == 364
    rate = agree / (agree + disagree)
    assert 0.66 < rate < 0.68
    assert rate < 0.75, "far too unreliable to label a side with"


def test_the_nbbo_quote_is_universally_available():
    """400/400 live prints carried a usable quote, which is why the exact method costs no
    extra request and needs no fallback to the naive rule."""
    usable, unusable = 400, 0
    assert unusable == 0
    assert usable == 400


def test_the_discarded_validation_sample_was_too_short():
    """The 64,338-print OHLC study spanned only 6 days. Pinned so it is not resurrected as
    evidence — this codebase has already retracted three findings that looked like that."""
    span_days, prints = 6, 64338
    assert span_days < 30, "far too short to measure a directional edge"
    assert prints > 60000, "a large sample over a tiny window is still a tiny window"


# ── The quote reaches the row and the DB ────────────────────────────────────────────────

def test_the_adapter_row_carries_the_quote():
    # 977.95 is 0.86 of the way to the ask. NOTE: my first version of this test used 977.80,
    # which is position 0.43 — genuinely INSIDE the mid band — and the classifier correctly
    # returned None. The test datum was wrong, not the code.
    row = uw.DarkPoolPrintRow(
        symbol="MU", price=977.95, size=100, premium=97795.0, venue="L",
        executed_at="2026-09-10T20:38:28Z", nbbo_bid=977.65, nbbo_ask=978.00,
    )
    assert uw.classify_dark_pool_side(row.price, row.nbbo_bid, row.nbbo_ask) == "buy"


def test_the_quote_fields_default_to_none_for_older_callers():
    """Defaulted, so constructing a row without a quote (tests, other code paths) still
    works and yields an honest None side rather than a crash."""
    row = uw.DarkPoolPrintRow(
        symbol="MU", price=977.8, size=100, premium=97780.0, venue="L",
        executed_at="2026-09-10T20:38:28Z",
    )
    assert row.nbbo_bid is None and row.nbbo_ask is None
    assert uw.classify_dark_pool_side(row.price, row.nbbo_bid, row.nbbo_ask) is None


def test_uw_returns_the_quote_as_strings_and_it_is_parsed():
    """Every numeric field in this payload arrives as a STRING ("977.65"). Parsing with
    float() directly would crash on None; _to_float handles both."""
    i = uw.__file__ and pathlib.Path(uw.__file__).read_text().index('nbbo_bid=_to_float(')
    assert i > 0


def test_the_persist_path_writes_the_quote():
    i = SCHED_SRC.index("def _persist_dark_pool_prints")
    blk = SCHED_SRC[i:SCHED_SRC.index("\ndef ", i + 10)]
    assert '"nbbo_bid"' in blk and '"nbbo_ask"' in blk


def test_the_raw_quote_is_stored_rather_than_a_precomputed_side():
    """So thresholds can be retuned against history without re-ingesting."""
    i = SCHED_SRC.index("def _persist_dark_pool_prints")
    blk = SCHED_SRC[i:SCHED_SRC.index("\ndef ", i + 10)]
    assert '"side"' not in blk


# ── The outcome row keeps the two prices apart ──────────────────────────────────────────

def test_the_model_has_all_three_new_outcome_columns():
    i = MODELS_SRC.index("class DarkPoolAlertOutcome")
    blk = MODELS_SRC[i:MODELS_SRC.index("\nclass ", i + 10)]
    for col in ("exec_price:", "live_price:", "side:"):
        assert col in blk, col


def test_alert_price_is_deliberately_unchanged():
    """Every forward-return column is denominated in alert_price. Redefining it would silently
    reinterpret all existing history."""
    i = MODELS_SRC.index("class DarkPoolAlertOutcome")
    blk = MODELS_SRC[i:MODELS_SRC.index("\nclass ", i + 10)]
    assert "alert_price: Mapped[float] = mapped_column(Float)" in blk


def test_the_recorder_accepts_and_stores_them():
    i = SCHED_SRC.index("def _record_dark_pool_alert_outcome")
    blk = SCHED_SRC[i:SCHED_SRC.index("\ndef ", i + 10)]
    assert "exec_price: float | None = None" in blk, "keyword-only with a None default"
    assert "side=side," in blk


def test_the_new_args_are_keyword_only_with_defaults():
    """So every pre-existing caller and test keeps working untouched."""
    i = SCHED_SRC.index("def _record_dark_pool_alert_outcome")
    sig = SCHED_SRC[i:SCHED_SRC.index(") -> None:", i)]
    assert "*," in sig
    assert sig.index("*,") < sig.index("exec_price")


def test_the_alert_populates_all_three_from_the_print_it_fired_on():
    i = SCHED_SRC.index("def check_dark_pool_alerts")
    blk = SCHED_SRC[i:SCHED_SRC.index("\ndef ", i + 10)]
    assert '"exec_price": biggest.price' in blk
    assert '"live_price": price' in blk
    assert "classify_dark_pool_side(" in blk


def test_the_side_is_computed_from_the_print_not_the_live_price():
    """THE WHOLE POINT. Passing `price` (live) here instead of `biggest.price` would
    reintroduce the 33%-wrong heuristic while looking correct."""
    i = SCHED_SRC.index("def check_dark_pool_alerts")
    blk = SCHED_SRC[i:SCHED_SRC.index("\ndef ", i + 10)]
    j = blk.index("classify_dark_pool_side(")
    call = blk[j:j + 160]
    assert "biggest.price" in call
    assert "biggest.nbbo_bid" in call and "biggest.nbbo_ask" in call


# ── The digest renders it, and renders nulls honestly ───────────────────────────────────

def _render(dp_rows, of_rows=()):
    lines = SCHED_SRC.splitlines(keepends=True)
    from datetime import date as _d, datetime as _dt, timezone as _tz
    from zoneinfo import ZoneInfo as _Z
    ns = {"datetime": _dt, "date": _d, "timezone": _tz, "ZoneInfo": _Z}
    for n in ast.parse(SCHED_SRC).body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in {
            "_money", "_fmt_hit_rate", "_render_flow_digest",
        }:
            exec("".join(lines[n.lineno - 1:n.end_lineno]), ns)  # noqa: S102
    acc = {"n": 255, "rate": 0.443, "adequate": True}
    return ns["_render_flow_digest"](
        list(dp_rows), list(of_rows), acc, acc, "session", _dt(2026, 9, 10, 4, 0),
    )


class _Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _dp(**kw):
    base = dict(
        symbol="MU", fired_at=__import__("datetime").datetime(2026, 9, 10, 16, 30),
        alert_price=977.71, qualifying_metric=5103398.04,
        exec_price=977.89, live_price=977.20, side="buy",
    )
    base.update(kw)
    return _Row(**base)


def test_the_digest_shows_the_execution_price():
    assert "$977.89" in _render([_dp()])[-1]


def test_the_digest_shows_the_live_price_at_that_moment():
    """PART 1 OF THE REQUEST — "the current price at that time"."""
    assert "$977.20" in _render([_dp()])[-1]


def test_the_digest_shows_the_side():
    assert "BUY" in _render([_dp()])[-1]
    assert "SELL" in _render([_dp(side="sell")])[-1]


def test_the_digest_shows_the_percentage_difference():
    txt = _render([_dp()])[-1]
    assert "+0.07%" in txt


def test_a_seller_priced_above_the_live_price_still_reads_SELL():
    """THE CASE THAT JUSTIFIES THE WHOLE FIX. A real shape: the print is 0.63% ABOVE the live
    price, so the naive rule says "buying", while the NBBO says the seller hit the bid. The
    digest must show the measured side, not the misleading arithmetic."""
    txt = _render([_dp(symbol="INTC", exec_price=24.10, live_price=23.95, side="sell")])[-1]
    assert "SELL" in txt
    assert "+0.63%" in txt, "the difference is still shown, as context"


def test_an_undeterminable_side_renders_a_dash_not_a_guess():
    txt = _render([_dp(side=None)])[-1]
    assert "BUY" not in txt and "SELL" not in txt
    assert "—" in txt


def test_a_legacy_row_with_no_new_fields_renders_without_crashing():
    """Rows written before this shipped carry NULL for all three. They must fall back to
    alert_price for Exec and show "—" elsewhere, never a fabricated side or a 0.00%."""
    txt = _render([_dp(exec_price=None, live_price=None, side=None)])[-1]
    assert "$977.71" in txt, "falls back to alert_price, which WAS the exec price for such rows"
    assert "0.00%" not in txt, "a missing comparison must not render as 'no difference'"


def test_the_side_is_not_falsy_tested():
    """`side` is a nullable STRING, so `r.side or "—"` is actually correct here — but the
    lookup form must still never map a real value to the dash."""
    i = SCHED_SRC.index("def _dp_side(r)")
    # Bound on the NEXT def, not on a blank line — these docstrings contain blank lines, so
    # "\n\n" truncated the slice to the signature and made the assertion vacuous-looking.
    fn = SCHED_SRC[i:SCHED_SRC.index("        def ", i + 10)]
    assert '"buy": "BUY"' in fn and '"sell": "SELL"' in fn


def test_the_html_headers_match_the_cell_count():
    """A header added without its cell shifts every column right."""
    html = _render([_dp()], [])[0]
    blk = html[html.index("Dark Pool ("):html.index("</table>", html.index("Dark Pool ("))]
    header, first = blk.split("</tr>")[0], blk.split("</tr>")[1]
    assert header.count("<th") == first.count("<td") > 0


def test_both_bodies_carry_the_new_columns():
    html, text = _render([_dp()], [])
    for token in ("977.89", "977.20", "BUY"):
        assert token in html, f"{token} missing from HTML"
        assert token in text, f"{token} missing from text"


def test_the_measured_caveat_is_recorded_in_the_source():
    """So nobody replaces the NBBO classifier with the live-price comparison later."""
    i = SCHED_SRC.index("def _dp_vs_live(r)")
    fn = SCHED_SRC[i:SCHED_SRC.index("        def ", i + 10)]
    assert "66.8%" in fn


# ── The migration exists ────────────────────────────────────────────────────────────────

def test_a_migration_ships_with_the_new_columns():
    """create_all() only creates MISSING TABLES — it never adds a column to an existing one.
    Both tables already exist, so the model change alone would raise UndefinedColumn."""
    root = pathlib.Path(__file__).resolve().parents[3]
    sql = (root / "scripts/migrations/2026-09-10_t377_darkpool_side.sql").read_text()
    for col in ("nbbo_bid", "nbbo_ask", "exec_price", "live_price", "side"):
        assert col in sql, col
    assert "ADD COLUMN IF NOT EXISTS" in sql, "must be idempotent"


def test_the_migration_does_not_backfill_a_fabricated_side():
    """Existing rows genuinely lack this data. Inventing a side would be the
    AUD-RANK-RSPLACEHOLDER error — a fabricated value a consumer then learns from."""
    root = pathlib.Path(__file__).resolve().parents[3]
    sql = (root / "scripts/migrations/2026-09-10_t377_darkpool_side.sql").read_text()
    assert "UPDATE" not in sql.upper().replace("UPDATED", "")
