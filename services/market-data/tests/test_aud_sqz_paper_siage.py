"""AUD-SQZ-PAPER-SIAGE (2026-11-10): the paper squeeze score refuses stale short interest,
the same 30-day rule the squeeze alerts apply."""
import json
from datetime import timedelta
from unittest.mock import patch

import src.services.paper_trading_engine as pte
import src.api.routes as routes


class _R:
    def __init__(self, blob):
        self.blob = blob

    def get(self, key):
        return json.dumps(self.blob)


def _score(si_date):
    blob = {"short_percent_of_float": 0.25, "short_ratio": 9.0, "short_interest_date": si_date}
    with patch.object(routes, "_get_redis", lambda: _R(blob)):
        return pte._squeeze_score_for("XYZ", 60.0)


def test_a_fresh_reading_scores():
    assert _score((pte._today_et_paper() - timedelta(days=10)).isoformat()) is not None


def test_a_reading_older_than_30_days_does_not_score():
    assert _score((pte._today_et_paper() - timedelta(days=31)).isoformat()) is None


def test_an_undated_reading_does_not_score():
    assert _score(None) is None
