"""SR-08 — a story we just received is not necessarily a story that just happened.

THE DEFECT. `news-intelligence`'s `_mark_hot` stores publication time but stamps `ts = now` with
a fresh 2-hour TTL. `signal-engine`'s hot-news decay reads `ts`. So an adverse story PUBLISHED on
September 1 and first ingested on October 1 received an October 1 timestamp and the full
first-hour compression — the platform reacting to a month-old fact as though it were breaking.
No age validation happened in the writer at all.

THE FIX IS NOT TO IGNORE SUCH A STORY. Newly-disclosed risk is still risk, and discarding it
would be the opposite error. It is treated as delayed EVIDENCE rather than a fresh CATALYST: a
reduced, separately-labelled compression, because the sharp move a fresh headline implies has
by construction already had its chance to happen.

The decay block is EXECUTED here, extracted from the real generator, so the test cannot pass
against a broken one. `signals.py` cannot be imported in this environment.
"""
import pathlib
import re
import textwrap

import pytest

_SIG = (pathlib.Path(__file__).resolve().parents[1]
        / "src" / "generators" / "signals.py").read_text()
_STORAGE = (pathlib.Path(__file__).resolve().parents[2]
            / "news-intelligence" / "src" / "services" / "storage.py").read_text()


def _delayed_constant() -> float:
    m = re.search(r"^_HOT_NEWS_DELAYED_COMPRESS = ([0-9.]+)$", _SIG, re.M)
    assert m, "_HOT_NEWS_DELAYED_COMPRESS is not a module-level constant"
    return float(m.group(1))


def _decay_snippet() -> str:
    """The real compression block, sliced on line boundaries and dedented."""
    i = _SIG.index("    if _hot_news_age_hours is not None and _hot_news_age_hours > 1.0:")
    # To the END of the block, which is the clip that closes it — not to the last line this
    # test happens to care about. A slice that stops early silently excludes the code under
    # test and the assertion then fails for the wrong reason.
    j = _SIG.index("    fused = float(np.clip(fused, 0.0, 1.0))", i)
    snippet = textwrap.dedent(_SIG[i:j])
    assert not snippet.startswith(" "), "dedent did not take — the slice is mid-line"
    return snippet


def _run(hot_news, *, fused=0.80, style_key="SWING", ingest_age_h=0.1):
    env = {
        "hot_news": hot_news, "fused": fused, "style_key": style_key,
        "_hot_news_age_hours": ingest_age_h, "reasons": {},
        "_HOT_NEWS_DELAYED_COMPRESS": _delayed_constant(),
    }
    exec(compile(_decay_snippet(), "<signals-snippet>", "exec"), env)
    return env["fused"], env["reasons"]


_FRESH = {"sentiment_label": "negative", "delayed_disclosure": False,
          "publication_age_hours_at_ingest": 0.2}
_DELAYED = {"sentiment_label": "negative", "delayed_disclosure": True,
            "publication_age_hours_at_ingest": 720.0}


# ── the witness ─────────────────────────────────────────────────────────────────────────

def test_a_month_old_story_is_not_compressed_like_a_breaking_one():
    """THE WITNESS: published September 1, ingested October 1."""
    fresh_fused, _ = _run(_FRESH)
    delayed_fused, _ = _run(_DELAYED)
    assert delayed_fused > fresh_fused, \
        "a delayed disclosure must not draw the full fresh-catalyst compression"


def test_a_delayed_story_is_still_compressed_at_all():
    """The opposite error. Newly-disclosed risk is still risk."""
    delayed_fused, _ = _run(_DELAYED)
    assert delayed_fused < 0.80, "a delayed adverse story must not be ignored"


def test_the_delayed_case_is_labelled_distinctly():
    _, reasons = _run(_DELAYED)
    assert reasons["hot_news_flag"] == "material_negative_delayed"
    _, fresh_reasons = _run(_FRESH)
    assert fresh_reasons["hot_news_flag"] == "material_negative"


def test_the_publication_age_travels_with_the_signal():
    """A compression nobody can explain afterwards is a compression nobody can audit."""
    _, reasons = _run(_DELAYED)
    assert reasons["hot_news_publication_age_hours"] == 720.0
    assert reasons["hot_news_delayed_disclosure"] is True


def test_a_pre_fix_flag_without_the_field_behaves_exactly_as_before():
    """Flags written before this change are still live inside their own TTL."""
    legacy = {"sentiment_label": "negative"}
    legacy_fused, reasons = _run(legacy)
    fresh_fused, _ = _run(_FRESH)
    assert legacy_fused == fresh_fused
    assert reasons["hot_news_flag"] == "material_negative"


def test_a_delayed_positive_story_still_applies_no_compression():
    """The delayed path must not accidentally start compressing non-negative news."""
    fused, reasons = _run({"sentiment_label": "positive", "delayed_disclosure": True})
    assert fused == 0.80
    assert reasons["hot_news_flag"] == "material_other"


def test_long_horizon_remains_exempt():
    fused, _ = _run(_DELAYED, style_key="LONG")
    assert fused == 0.80


def test_no_news_is_not_delayed_news():
    fused, reasons = _run(None)
    assert fused == 0.80 and reasons["hot_news_flag"] == "none"


# ── the writer ──────────────────────────────────────────────────────────────────────────

def test_the_writer_records_publication_age_and_classifies_it():
    i = _STORAGE.index("payload = json.dumps({")
    block = _STORAGE[i:_STORAGE.index("})", i)]
    assert '"publication_age_hours_at_ingest"' in block
    assert '"delayed_disclosure"' in block
    assert '"published_at": pub_iso,' in block, "both clocks must still be stored separately"
    assert '"ingested_at": now_iso,' in block


def test_an_unknown_publication_time_is_none_not_zero():
    """`None` means the source gave no publication time. Zero would mean 'published exactly
    now', which is a measurement nobody made."""
    i = _STORAGE.index('"publication_age_hours_at_ingest"')
    block = _STORAGE[i:_STORAGE.index("})", i)]
    assert "if _pub_age_h is not None else None" in block
    assert "None if _pub_age_h is None else bool" in block


def test_the_threshold_is_a_named_constant_with_its_reasoning():
    m = re.search(r"^_DELAYED_DISCLOSURE_HOURS = ([0-9.]+)$", _STORAGE, re.M)
    assert m, "the delayed-disclosure threshold must be a named module constant"
    assert float(m.group(1)) > 0


def test_the_delayed_compression_is_weaker_than_both_fresh_strengths():
    """It sits between the fresh-catalyst strengths and no compression at all. A value outside
    that range would be a different policy than the one documented."""
    delayed = _delayed_constant()
    assert 0.85 < delayed < 1.0


def test_the_delayed_strength_is_not_presented_as_calibrated():
    i = _SIG.index("_HOT_NEWS_DELAYED_COMPRESS = ")
    comment = _SIG[max(0, i - 900):i]
    assert "not tuned" in comment.lower() or "not a calibrated" in comment.lower(), \
        "an uncalibrated policy constant must say so where it is defined"
