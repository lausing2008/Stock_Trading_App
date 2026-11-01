"""AUD-DQ-NAMEONLY (2026-11-01): every _DQ_CHECKS entry must be one the runner can dispatch.

`check_llm_daily_budget` and `capture_prospective_estimates` were registered with only a name
and a description. The runner fell through to `check["query"]`, raised KeyError on both on every
run from 2026-10-05/06 until this fix, and neither job was watched — one of them guards a capture
whose missed days are unrecoverable. The existing tests only checked that the names appeared.

The list holds lambdas (gauge `counter_fn`), so it is read from the AST, not literal_eval.
"""
import ast
import pathlib

_SRC = (pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py").read_text()

_REQUIRED = {
    None: {"query", "max_age_hours", "is_date"},
    "job_status": {"job_name", "max_age_hours", "is_date"},
    "ratio": {"numerator_key", "denominator_key", "min_ratio", "min_denominator"},
}


def _checks():
    tree = ast.parse(_SRC)
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.AnnAssign)
                and isinstance(n.target, ast.Name) and n.target.id == "_DQ_CHECKS")
    out = []
    for d in node.value.elts:
        assert isinstance(d, ast.Dict)
        keys = {k.value: v for k, v in zip(d.keys, d.values)}
        src = keys.get("source")
        out.append((keys["name"].value, set(keys), src.value if src is not None else None))
    return out


def test_the_list_is_found_and_non_trivial():
    assert len(_checks()) > 40


def test_every_check_has_the_keys_its_branch_reads():
    bad = []
    for name, keys, source in _checks():
        assert {"name", "description"} <= keys, name
        if source == "gauge":
            if not ({"counter_key", "counter_fn"} & keys):
                bad.append((name, "gauge needs counter_key or counter_fn"))
            continue
        assert source in _REQUIRED, f"{name}: unknown source {source!r}"
        missing = _REQUIRED[source] - keys
        if missing:
            bad.append((name, sorted(missing)))
    assert bad == []


def test_names_are_unique():
    names = [n for n, _, _ in _checks()]
    assert len(names) == len(set(names))


def test_the_two_previously_name_only_checks_are_now_watched():
    by = {n: (k, s) for n, k, s in _checks()}
    keys, source = by["check_llm_daily_budget"]
    assert source == "job_status" and "'llm_daily_budget_check'" not in keys
    assert 'id="llm_daily_budget_check"' in _SRC or "id='llm_daily_budget_check'" in _SRC
    seg = _SRC[_SRC.index('"name": "capture_prospective_estimates"'):][:600]
    assert "FROM estimate_snapshots" in seg and "captured_at" in seg


def test_the_capture_window_allows_for_a_midnight_stamped_daily_run():
    """captured_at is the capture DAY at 00:00 and the run is at 11:00 UTC, so a healthy series
    is up to 35h old just before the next run; anything under that alarms every morning."""
    seg = _SRC[_SRC.index('"name": "capture_prospective_estimates"'):][:600]
    max_age = int(seg.split('"max_age_hours":')[1].split(",")[0])
    assert 35 < max_age <= 40
