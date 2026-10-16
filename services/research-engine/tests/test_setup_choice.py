"""Scenario tags cannot silently pick an invalid or ambiguous numerical plan."""
import pytest
from src.copilot.setup_choice import extract


@pytest.mark.parametrize('text', [
    '<answer>Use a setup.</answer>',
    '<plan_setup id="pullback"/><plan_setup id="wait"/>',
    '<plan_setup id="invented"/>',
    '<plan_setup id="pullback" extra="override"/>',
    '<plan_setup id="pullback">',
])
def test_invalid_or_ambiguous_selection_is_refused(text):
    _, chosen, error = extract(text, [{'id':'pullback','state':'conditional'}])
    assert chosen is None and error


@pytest.mark.parametrize('state', ['blocked','stale','unavailable'])
def test_model_cannot_overrule_setup_state(state):
    _, chosen, error = extract('<plan_setup id="pullback"/>', [{'id':'pullback','state':state}])
    assert chosen is None and error


def test_valid_selection_is_removed_from_prose_and_wait_does_not_recommend_a_blocked_entry():
    clean, chosen, error = extract('<answer>Review the exit.</answer><plan_setup id="holding"/>', [{'id':'holding','state':'exit_triggered'}])
    assert clean == '<answer>Review the exit.</answer>' and chosen == 'holding' and error is None
    _, chosen, error = extract('<plan_setup id="wait"/>', [{'id':'pullback','state':'blocked'}])
    assert chosen == 'wait' and error is None
