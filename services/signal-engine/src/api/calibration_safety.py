"""Fail-closed promotion boundary for incomplete legacy calibration studies (R11–R14).

Existing diagnostics remain available. These studies have neither immutable all-candidate
inputs nor complete net-cost trade replay, so none can establish a production improvement.
This is intentionally not an environment toggle that can accidentally re-enable writes.
"""
from functools import wraps

POLICY_VERSION = "calibration_promotion_v1"
MISSING_REQUIREMENTS = (
    "immutable_all_candidate_policy_snapshots",
    "verified_forward_outcomes",
    "whole_session_purged_holdout",
    "complete_entry_exit_cost_replay",
    "positive_net_expectancy_and_risk_validation",
)


def promotion_status():
    return {"status": "research_only", "applied": False, "promoted": False,
            "would_apply": False, "policy_version": POLICY_VERSION,
            "reason": "Production promotion withheld: legacy studies do not replay a complete executable strategy.",
            "missing_requirements": list(MISSING_REQUIREMENTS),
            "existing_settings_changed": False}


def research_only(function):
    """Guard the actual registered endpoint before any Redis/file/DB mutation."""
    @wraps(function)
    def guarded(*args, **kwargs):
        return {**promotion_status(), "mechanism": function.__name__}
    return guarded


def evidence_eligible(row):
    """One strict population contract for legacy diagnostic outcome readers."""
    get = row.get if isinstance(row, dict) else lambda k: getattr(row, k, None)
    signal, entry, exit_ = get("signal_date"), get("entry_date"), get("exit_date")
    return (get("evidence_status") == "verified" and get("is_correct") is not None
            and signal is not None and entry is not None and exit_ is not None
            and signal < entry <= exit_)
