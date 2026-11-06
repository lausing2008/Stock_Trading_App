"""Offline regression for the release acceptance contract; no service access."""
import copy
import importlib.util
from pathlib import Path

import pytest


path = (Path(__file__).resolve().parents[3] / "docs/audits/evidence/"
        "2026-10-30-valuation-phases/verify_phases.py")
spec = importlib.util.spec_from_file_location("phases_acceptance", path)
acceptance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acceptance)


def snapshot():
    return {"id": 1, "version": 1, "status": "complete",
            "coverage": {"sampled": 1}, "valuation": {"rows": [{"symbol": "ADBE"}]}}


def test_supersession_preserves_content_but_changes_status():
    before = snapshot()
    acceptance.assert_frozen_history(before, {**before, "status": "superseded"}, 2)
    acceptance.assert_frozen_history(before, copy.deepcopy(before), 1)


@pytest.mark.parametrize("field,value", [
    ("valuation", {"rows": []}), ("coverage", {"sampled": 2}), ("version", 2),
    ("status", "failed"),
])
def test_history_check_rejects_changed_content_or_invalid_status(field, value):
    before = snapshot()
    after = {**copy.deepcopy(before), "status": "superseded", field: value}
    with pytest.raises(AssertionError):
        acceptance.assert_frozen_history(before, after, 2)


def test_identical_report_reuse_cannot_change_status():
    before = snapshot()
    with pytest.raises(AssertionError):
        acceptance.assert_frozen_history(before, {**before, "status": "superseded"}, 1)
