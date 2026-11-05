"""AUD-DIGEST-CLOSEDSESSION: no session is used after the `with` block that closed it.

send_morning_digest passed its closed `session` to _may_send after `with SessionLocal() as
session:` had exited. A closed Session silently checks out a new connection and begins a
transaction that nothing ends; on 2026-11-05 that connection sat idle in transaction until
PostgreSQL killed it, and garbage collection released it 15 minutes after it was opened. The
db.long_hold log caught it. This scans every market-data module for the same shape.
"""
import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"


def _uses_after_with(tree):
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for w in ast.walk(fn):
            if not isinstance(w, ast.With):
                continue
            for item in w.items:
                ce = item.context_expr
                if not (isinstance(ce, ast.Call) and "Session" in ast.unparse(ce.func)
                        and isinstance(item.optional_vars, ast.Name)):
                    continue
                var, end = item.optional_vars.id, w.end_lineno
                rebinds = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Name) and n.id == var
                           and isinstance(n.ctx, ast.Store) and n.lineno > end]
                for n in ast.walk(fn):
                    if (isinstance(n, ast.Name) and n.id == var and isinstance(n.ctx, ast.Load)
                            and n.lineno > end and not any(r <= n.lineno for r in rebinds)):
                        yield fn.name, w.lineno, n.lineno
                        break


def test_no_session_is_used_after_its_with_block():
    hits = []
    for path in sorted(SRC.rglob("*.py")):
        for name, opened, used in _uses_after_with(ast.parse(path.read_text())):
            hits.append(f"{path.relative_to(SRC)}:{used} {name}() uses the session closed at line {opened}'s block")
    assert not hits, "\n".join(hits)


def test_the_scan_catches_the_original_shape():
    bad = ast.parse(
        "def f():\n"
        "    with SessionLocal() as session:\n"
        "        users = session.execute(q)\n"
        "    for u in users:\n"
        "        _may_send(session, u, 'morning_digest')\n")
    assert list(_uses_after_with(bad)) == [("f", 2, 5)]
