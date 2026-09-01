"""Regression tests for MPE-04's _load_options_snapshots() (trainer.py) — the point-in-time
options-flow query feeding build_features()'s opt_cp_ratio/opt_whale_count columns.

trainer.py can't be imported directly in this local dev environment (its import chain pulls in
xgboost/torch/lightgbm, none of which are installed here) — source-text regression checks,
matching this repo's own established pattern for functions in this exact file with this exact
constraint (e.g. test_meta_trainer.py's own docstring explains the identical issue).
"""
import pathlib

_TRAINER_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "training" / "trainer.py"
_TRAINER_SOURCE = _TRAINER_PATH.read_text()


def _function_body() -> str:
    start = _TRAINER_SOURCE.index("def _load_options_snapshots(")
    end = _TRAINER_SOURCE.index('\n\ndef _artifact_path(', start)
    return _TRAINER_SOURCE[start:end]


def test_function_exists():
    assert "def _load_options_snapshots(symbol: str) -> list[dict]:" in _TRAINER_SOURCE


def test_queries_options_flow_snapshots_joined_to_stocks_by_symbol():
    body = _function_body()
    assert "FROM options_flow_snapshots ofs" in body
    assert "JOIN stocks s ON s.id = ofs.stock_id" in body
    assert "WHERE s.symbol = :sym" in body


def test_selects_the_two_real_columns_the_ablation_harness_actually_tests():
    body = _function_body()
    assert "ofs.cp_ratio" in body
    assert "ofs.whale_count" in body


def test_orders_by_as_of_ascending_matching_pit_join_convention():
    """merge_asof(direction='backward') in builder.py requires its right-side keys to already
    be sorted — _load_fund_snapshots() enforces this via ORDER BY snapshot_date; this function
    must do the same for as_of."""
    body = _function_body()
    assert "ORDER BY ofs.as_of" in body


def test_returned_dict_shape_matches_what_builder_pys_join_expects():
    """The returned list-of-dicts must use the exact keys OPTIONS_COLUMNS/the merge_asof join
    in builder.py reads: snapshot_date, opt_cp_ratio, opt_whale_count."""
    body = _function_body()
    assert '"snapshot_date": str(r.snapshot_date),' in body
    assert '"opt_cp_ratio": r.cp_ratio,' in body
    assert '"opt_whale_count": r.whale_count,' in body


def test_fails_open_to_an_empty_list_on_any_exception():
    body = _function_body()
    assert "except Exception as exc:" in body
    assert "return []" in body


def test_symbol_is_uppercased_before_the_query_matching_fund_snapshots_convention():
    body = _function_body()
    assert "symbol.upper()" in body
