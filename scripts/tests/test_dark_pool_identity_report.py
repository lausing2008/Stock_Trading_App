import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location('identity_report_tested', Path(__file__).resolve().parents[1] / 'dark_pool_identity_report.py')
diagnostic = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnostic)


@pytest.mark.parametrize('params', [{'days': 0}, {'days': 31}, {'limit': 0}, {'limit': 2001}])
def test_invalid_scope_is_rejected_before_any_query(params):
    with pytest.raises(ValueError):
        diagnostic.report(None, **params)


class Session:
    def __init__(self, fail=False):
        self.calls = []
        self.rolled_back = False
        self.fail = fail
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def execute(self, query, params=None):
        self.calls.append((str(query), params))
        if str(query) == diagnostic.QUERY:
            if self.fail:
                raise RuntimeError('postgresql://private-password@private-host/private-db')
            return SimpleNamespace(mappings=lambda: SimpleNamespace(one=lambda: {
                'ambiguous_ids_in_window': 12, 'ids_reviewed': 5, 'retained_versions_reviewed': 10}))
    def rollback(self): self.rolled_back = True


def test_cli_enforces_read_only_and_timeout_and_returns_counts_only(monkeypatch, capsys):
    session = Session()
    monkeypatch.setitem(sys.modules, 'db', SimpleNamespace(SessionLocal=lambda: session))
    monkeypatch.setattr(sys, 'argv', ['identity_report', '--limit', '5'])
    diagnostic.main()
    result = json.loads(capsys.readouterr().out)
    assert result['limited'] and result['counts']['ids_reviewed'] == 5
    assert session.calls[0][0] == 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'
    assert session.calls[1][0] == "SET LOCAL statement_timeout = '10s'"
    assert session.calls[2][1]['limit'] == 5 and session.rolled_back


def test_failure_rolls_back_without_exposing_connection_details(monkeypatch, capsys):
    session = Session(fail=True)
    monkeypatch.setitem(sys.modules, 'db', SimpleNamespace(SessionLocal=lambda: session))
    monkeypatch.setattr(sys, 'argv', ['identity_report'])
    with pytest.raises(SystemExit) as error:
        diagnostic.main()
    assert error.value.code == 1 and session.rolled_back
    captured = capsys.readouterr().out
    assert json.loads(captured) == {'status': 'query_failed', 'error_type': 'RuntimeError'}
    assert 'private' not in captured
