"""AUD-EARNCOMPRESS-PROXIMITY (2026-09-22) — pre-earnings compression suspended for an A/B.

BUY signals, 5-day forward return, all cohorts over the same window:

    caution (DTE 0-2)  n=77    +1.72%   sd 8.62   Sharpe  0.200
    note    (DTE 3-5)  n=43    +0.32%   sd 9.34   Sharpe  0.034
    watch   (DTE 6-10) n=104   -0.14%   sd 4.79   Sharpe -0.029
    (none)             n=9575  -1.40%   sd 7.96   Sharpe -0.176

The gradient is monotone in earnings proximity and `caution` is best on BOTH raw return and
Sharpe — yet compression is TIGHTEST there (ec[2] = 0.60-0.65, a 35-40% haircut toward 0.50).
Independently, the platform's own filter_audit scores earnings_warning as its single most
harmful filter (+2.90pp alpha edge on the trades it blocks, n=149).

THE CONFOUND, stated plainly: those cohorts are survivorship-filtered. A signal compressed by
0.60 that STILL cleared BUY was stronger before compression, which biases the compressed buckets
upward. The table is suggestive, not proof, and no amount of re-querying existing data settles
it — only running with compression off and comparing does. That is what the flag is for, and it
is why this is framed as an experiment rather than a fix.

Two properties below are load-bearing:

  * TAGGING SURVIVES THE FLAG. `reasons["earnings_warning"]` is still written when compression
    is skipped. filter_audit measures this cohort by that reason key, so suppressing the tag
    would make the very A/B this change exists to run invisible.
  * SA-25's SHORT DTE<=2 GUARD IS NOT GATED. It carries the highest variance of any cohort
    (sd 9.66) and guards a genuine coin-flip binary event on a 5-day trade — a risk control,
    not a return bet.
"""
import pathlib
import re

_SRC = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "generators" / "signals.py"
).read_text()


def _flag_default() -> bool:
    line = next(
        ln for ln in _SRC.split("\n") if ln.startswith("_EARNINGS_COMPRESSION_ENABLED")
    )
    return "True" in line


def test_compression_is_disabled_by_default():
    """The regression guard: re-enabling without running the A/B fails here."""
    assert _flag_default() is False, (
        "_EARNINGS_COMPRESSION_ENABLED must stay False until the A/B has actually run"
    )


def test_all_three_compressing_bands_are_gated():
    """caution/note/watch must each be behind the flag — gating only one would leave a partial,
    uninterpretable experiment."""
    gated = _SRC.count("if _EARNINGS_COMPRESSION_ENABLED:\n                    fused = 0.5 + (fused - 0.5) * adj_mult")
    assert gated == 3, f"expected 3 gated compression bands, found {gated}"


def test_no_ungated_band_compression_remains():
    """Catches a band whose compression was left outside the flag."""
    ungated = re.findall(
        r"^(?!\s*#)\s{16}fused = 0\.5 \+ \(fused - 0\.5\) \* adj_mult", _SRC, flags=re.M
    )
    assert ungated == [], f"found {len(ungated)} ungated band compression(s)"


def test_the_cohort_is_still_tagged_when_compression_is_skipped():
    """THE property that keeps the experiment measurable. filter_audit groups by this reason
    key; if the tag moved inside the flag, the A/B would produce no comparison at all."""
    for tag in ("caution", "note", "watch"):
        marker = f'reasons["earnings_warning"] = "{tag}"'
        idx = _SRC.index(marker)
        # Walk back to the governing flag check and confirm the tag is NOT inside it.
        preceding = _SRC[:idx].rsplit("if _EARNINGS_COMPRESSION_ENABLED:", 1)[-1]
        assert "reasons[" not in preceding.split("\n")[0], "tag appears bound to the flag line"
        tag_indent = len(marker) - len(marker.lstrip())
        line = next(ln for ln in _SRC.split("\n") if marker in ln)
        assert (len(line) - len(line.lstrip())) == 16, (
            f"{tag} tag is indented {len(line) - len(line.lstrip())}, expected 16 — it looks "
            "nested inside the flag block, which would hide the cohort from filter_audit"
        )


def test_short_style_binary_event_guard_is_deliberately_not_gated():
    """SA-25 guards a coin-flip event on a 5-day trade and carries the highest variance of any
    cohort (sd 9.66). It is a risk control, not a return bet, and must keep firing."""
    start = _SRC.index('reasons["earnings_warning"] = "short_imminent_event"')
    block = _SRC[start - 700:start]
    assert "fused = 0.5 + (fused - 0.5) * adj_short" in block
    assert "_EARNINGS_COMPRESSION_ENABLED" not in block


def test_bull_beater_path_is_untouched():
    """The SA-7 reliable-beater branch already SKIPS compression and adds a boost; it was
    already doing the right thing and must not be swept up in this change."""
    start = _SRC.index('reasons["earnings_warning"] = "bull_beater"')
    block = _SRC[start - 400:start]
    assert "fused = float(np.clip(fused + 0.03, 0.0, 1.0))" in block
    assert "_EARNINGS_COMPRESSION_ENABLED" not in block
