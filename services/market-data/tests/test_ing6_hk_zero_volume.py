"""AUD-ING6-HKZEROVOLUME — Area 6 of the 2026-09-08 ingestion audit.

`validate_ohlcv()`'s `volume > 0` rule encoded a US-liquid-equity assumption ("real trading
always has nonzero volume") that is FALSE for thinly-traded HK small caps, where a zero-volume
day is a legitimate no-trade session carrying a real price.

Measured against the real 3-year fetch through the real validator:

    1671.HK   fetched 735   kept 277   DROPPED 458   (448 of them zero-volume)
    0117.HK   fetched 735   kept 320   DROPPED 415   (398 of them zero-volume)

Zero-volume share by liquidity: 0700.HK / 0005.HK / 9988.HK 0.6-1.2%, 0117.HK 26.9%,
1671.HK 48.6%. The rule is correct for liquid names and badly wrong for illiquid ones.

It was also SELF-CONCEALING: the weekly force=True refresh re-fetched all 735 bars and re-dropped
the same 458 every week, so the gap could never heal.
"""
import pandas as pd
import pytest

from src.services.ingestion import validate_ohlcv


def _bars(volumes):
    """A frame of otherwise-valid bars with the given volumes."""
    n = len(volumes)
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="D"),
        "open": [10.0] * n, "high": [11.0] * n, "low": [9.0] * n, "close": [10.5] * n,
        "volume": volumes,
    })


# ── The volume gate ──────────────────────────────────────────────────────────────────────

def test_zero_volume_bars_are_kept_when_allowed():
    """THE CORE FIX. A zero-volume day on an illiquid listing is a real no-trade session."""
    kept = validate_ohlcv(_bars([1000, 0, 0, 2000]), "1671.HK", allow_zero_volume=True)
    assert len(kept) == 4


def test_zero_volume_bars_are_still_dropped_when_not_allowed():
    """US daily stays strict — a zero-volume regular-session bar on a liquid US listing really
    is a bad bar, and loosening it there would discard a genuine signal."""
    kept = validate_ohlcv(_bars([1000, 0, 0, 2000]), "AAPL", allow_zero_volume=False)
    assert len(kept) == 2


def test_the_measured_1671hk_case():
    """Replays the real shape: 735 bars of which 448 are zero-volume. Under the old rule only
    287 survive the volume gate; under the fix all 735 do."""
    vols = [0] * 448 + [1000] * 287
    strict = validate_ohlcv(_bars(vols), "1671.HK", allow_zero_volume=False)
    relaxed = validate_ohlcv(_bars(vols), "1671.HK", allow_zero_volume=True)
    assert len(strict) == 287, "the old behaviour discarded 61% of the series"
    assert len(relaxed) == 735


# ── The invariants that must NOT be relaxed ──────────────────────────────────────────────

def test_ohlc_ordering_still_enforced_even_when_zero_volume_is_allowed():
    """This fix relaxes ONLY the volume gate. A bar with open above high is still invalid in
    every market — relaxing that would reintroduce exactly the class of row the SPY anomaly
    turned out to be."""
    df = _bars([0, 0])
    df.loc[0, "open"] = 99.0  # open > high
    kept = validate_ohlcv(df, "1671.HK", allow_zero_volume=True)
    assert len(kept) == 1


def test_low_above_close_still_rejected_when_zero_volume_allowed():
    df = _bars([0, 0])
    df.loc[0, "low"] = 50.0  # low > close
    assert len(validate_ohlcv(df, "1671.HK", allow_zero_volume=True)) == 1


def test_nonpositive_prices_still_rejected_when_zero_volume_allowed():
    """A zero PRICE is always invalid; only a zero VOLUME becomes acceptable."""
    df = _bars([0, 0])
    df.loc[0, ["open", "high", "low", "close"]] = 0.0
    assert len(validate_ohlcv(df, "1671.HK", allow_zero_volume=True)) == 1


def test_negative_volume_is_not_made_acceptable():
    """`allow_zero_volume` means ZERO, not negative. A negative volume is corrupt data in any
    market and was never the thing being permitted.

    An earlier version of this test had a weak `or` that passed trivially, and the naive
    implementation (`if not allow_zero_volume: df = df[df["volume"] > 0]`) genuinely DID keep a
    volume of -5 — skipping the check entirely rather than lowering its floor. Assert on the
    surviving values directly.
    """
    kept = validate_ohlcv(_bars([-5, 0, 1000]), "1671.HK", allow_zero_volume=True)
    vols = sorted(kept["volume"].tolist())
    assert vols == [0, 1000], f"expected the zero kept and the negative dropped, got {vols}"


# ── The caller's market routing ──────────────────────────────────────────────────────────

def _routing(market: str, timeframe: str) -> bool:
    """Mirrors ingestion.py's allow_zero_volume expression."""
    return (market == "US" and timeframe not in ("1d", "1w")) or market == "HK"


def test_hk_daily_now_allows_zero_volume():
    assert _routing("HK", "1d") is True
    assert _routing("HK", "1w") is True


def test_us_daily_still_strict():
    """The regression that would matter most: silently loosening the US daily invariant."""
    assert _routing("US", "1d") is False
    assert _routing("US", "1w") is False


def test_us_intraday_prepost_behaviour_unchanged():
    """T230-CHARTING-PREMARKET's original reason for this parameter must still hold."""
    for tf in ("1m", "5m", "15m", "1h"):
        assert _routing("US", tf) is True


def test_hk_intraday_also_allowed():
    """HK has no pre/post session, but an illiquid HK intraday bar is at least as likely to be
    zero-volume as a daily one — the relaxation must not be daily-only."""
    assert _routing("HK", "5m") is True


def test_routing_expression_matches_the_source():
    """Guards the local mirror above against drifting from the real expression.

    AUD-ING6-MARKETINFER updated this: the rule now keys off `_effective_market`, derived from
    the symbol suffix the same way adapter selection is, instead of the bare `market` parameter.
    The parameter alone was wrong because ingest_universe() never passes one — so every HK
    symbol reaching this code through that path was held to the strict US volume gate. The
    local mirror's semantics for an explicit market are unchanged, which is why the behavioural
    tests above still pass untouched.
    """
    import pathlib
    import src.services.ingestion as ing
    src = pathlib.Path(ing.__file__).read_text()
    assert 'or market == "HK"' in src
    assert '(_effective_market == "US" and timeframe not in ("1d", "1w"))' in src
    assert 'or _effective_market == "HK"' in src
    # And the effective market must be derived, not assumed.
    assert '_effective_market = "HK" if (symbol.endswith(".HK") or market == "HK") else market' in src


def test_docstring_no_longer_asserts_the_false_claim():
    """The old docstring stated "real trading always has nonzero volume there" as fact. Leaving
    that in place would tell the next reader the opposite of what the code now does."""
    import pathlib
    import src.services.ingestion as ing
    src = pathlib.Path(ing.__file__).read_text()
    doc = src[src.index("def validate_ohlcv"):src.index("def _classify_session")]
    assert "AUD-ING6-HKZEROVOLUME" in doc
    assert "US-LIQUID-EQUITY assumption" in doc
