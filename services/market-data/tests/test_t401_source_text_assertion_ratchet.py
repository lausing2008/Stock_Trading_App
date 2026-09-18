"""AUD-T401-SOURCETEXTTESTS — a ratchet on source-text assertions that pin NUMBERS.

WHY A RATCHET RATHER THAN A REWRITE. This repo has ~2,700 source-text assertions across 283
test files. Converting them wholesale would be enormous churn for mixed benefit, because many
are legitimate: "this function must NOT call yfinance" is a design constraint that no behavioural
test can express, and scheduler wiring genuinely cannot be executed in this environment.

The dangerous subset is narrower and identifiable: assertions that pin a NUMBER — a threshold, a
multiplier, a boundary. Those are dangerous precisely because they look rigorous while being
unable to detect the defect they name.

THE PROOF, from this codebase, on my own work (2026-09-18). The AUD-C04 stale-price gate was
first covered by:

    assert "func.max(Price.ts) >= cutoff_fresh" in body

which PASSED when the expression was sabotaged to `>= cutoff_fresh - timedelta(days=99999)` —
the substring survived, so the test saw nothing while the gate was completely defeated. Rewritten
as a pure function tested as a number, the same sabotage fails two tests immediately.

THE RULE:
  * A threshold is a NUMBER. Test it as one — extract it into a pure function if you must.
  * Source text is for things behaviour cannot express: "must not call X", "these two call sites
    must use the same helper", "this job is registered with the scheduler".
  * If an assertion contains a comparison or assignment involving a number, it is almost
    certainly the first kind wearing the costume of the second.

This test does not fix the existing 181. It guarantees they cannot become 182, so the number
only moves down — and anyone adding one must either write a real test or consciously raise the
baseline with a reason.
"""
import pathlib
import re

# Measured 2026-09-18. LOWER THIS when you convert assertions; raising it needs a reason in the
# commit message, because every increment is a test that cannot fail for the bug it names.
_BASELINE = 181

_REPO = pathlib.Path(__file__).resolve().parents[3]

_ASSERT_RE = re.compile(r'assert\s+(["\'])([^"\']*?[0-9][^"\']*?)\1\s+(?:not\s+)?in\s+\w')
# A comparison, an assignment, or a multiplication involving a literal number — i.e. a THRESHOLD,
# as opposed to an identifier that merely happens to contain a digit (`sha256`, `return_5d`).
_NUMERIC_RE = re.compile(r'[<>=]=?\s*-?[0-9.]|=\s*-?[0-9.]+|\*\s*[0-9.]+')


def _offenders() -> list[tuple[str, int, str]]:
    out: list[tuple[str, int, str]] = []
    for f in sorted((_REPO / "services").glob("*/tests/test_*.py")):
        text = f.read_text()
        if "read_text()" not in text and "_SOURCE" not in text:
            continue  # not a source-text test file at all
        for lineno, line in enumerate(text.split("\n"), 1):
            m = _ASSERT_RE.search(line)
            if m and _NUMERIC_RE.search(m.group(2)):
                out.append((str(f.relative_to(_REPO)), lineno, m.group(2)[:80]))
    return out


def test_source_text_numeric_assertions_do_not_increase():
    found = _offenders()
    if len(found) > _BASELINE:
        added = "\n".join(f"    {f}:{n}  assert \"{s}\"" for f, n, s in found[-8:])
        raise AssertionError(
            f"Source-text assertions pinning a NUMBER rose to {len(found)} (baseline {_BASELINE}).\n\n"
            f"A threshold is a number — test it as one. `assert \"x >= 5\" in source` passes when "
            f"the code is changed to `x >= 5 - 99999`, because the substring survives.\n\n"
            f"Extract the threshold into a pure function and assert its VALUE, as\n"
            f"test_t406_tier384_remainder.py does for the stale-price entry gate.\n\n"
            f"Recent matches:\n{added}\n"
        )


def test_the_baseline_is_not_silently_slack():
    """A ratchet set far above the real count ratchets nothing. If conversions have brought the
    real number well below the baseline, tighten it — otherwise the guard quietly stops guarding."""
    found = _offenders()
    assert len(found) >= _BASELINE - 15, (
        f"Only {len(found)} numeric source-text assertions remain against a baseline of "
        f"{_BASELINE}. Lower _BASELINE to {len(found)} so the ratchet keeps its grip."
    )
