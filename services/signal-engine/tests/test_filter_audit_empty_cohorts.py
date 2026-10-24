"""Execute the real aggregation block, without external services or copied verdict logic."""
import ast
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[1] / 'src/api/analytics.py'


def aggregate(cohorts):
    tree = ast.parse(SOURCE.read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'filter_audit')
    mean = next(n for n in function.body if isinstance(n, ast.FunctionDef) and n.name == '_mean_pct')
    start = next(i for i, n in enumerate(function.body) if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == 'by_filter' for t in n.targets))
    end = next(i for i in range(start, len(function.body))
               if isinstance(function.body[i], ast.Expr) and isinstance(function.body[i].value, ast.Call)
               and ast.unparse(function.body[i].value.func) == 'by_filter.sort')
    namespace = dict(all_filter_names=list(cohorts), filter_buckets=cohorts,
                     filter_alpha={k: {'active': [], 'inactive': []} for k in cohorts},
                     _OUTCOME_WIN_HURDLE_PCT=0.005)
    exec(compile(ast.Module(body=[mean, *function.body[start:end + 1]], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace['by_filter']


@pytest.mark.parametrize('active,inactive', [([], [1.0]), ([1.0], []), ([], [])])
def test_missing_cohort_never_manufactures_an_edge_or_verdict(active, inactive):
    row, = aggregate({'news_sentiment': {'active': active, 'inactive': inactive}})
    assert row['edge_pct'] is None
    assert row['verdict'] == 'insufficient_evidence'
    assert row['comparison_status'] == 'insufficient_evidence'
    assert row['comparison_reason']
    assert row['n_active'] == len(active) and row['n_inactive'] == len(inactive)
    assert row['alpha_edge_pct'] is None and row['alpha_verdict'] is None


@pytest.mark.parametrize('active,inactive,edge,verdict', [
    ([1.0], [-1.0], 100, 'harmful'),
    ([-1.0], [1.0], -100, 'predictive'),
    ([-1.0], [-1.0], 0, 'weak'),
])
def test_measured_including_zero_win_rate_keeps_existing_thresholds(active, inactive, edge, verdict):
    row, = aggregate({'test': {'active': active, 'inactive': inactive}})
    assert row['edge_pct'] == edge and row['verdict'] == verdict
    assert row['comparison_status'] == 'measured' and row['comparison_reason'] is None


def test_missing_edges_sort_after_measured_comparisons():
    rows = aggregate({'unknown': {'active': [], 'inactive': [1.0]},
                      'harmful': {'active': [1.0], 'inactive': [-1.0]},
                      'predictive': {'active': [-1.0], 'inactive': [1.0]}})
    assert [r['filter'] for r in rows] == ['predictive', 'harmful', 'unknown']


def test_cache_namespace_does_not_reuse_old_fabricated_verdicts():
    assert 'signals:cache:filter_audit:v2:' in SOURCE.read_text()
