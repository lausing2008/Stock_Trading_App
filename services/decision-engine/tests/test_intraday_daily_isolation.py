"""Intraday Stage C daily isolation, decision-engine side (design §10).

behaviour: /decide refuses a non-daily style before fanning out. It used to forward the raw style
to signal-engine, which regenerated, persisted and returned the SWING signal."""
import asyncio
from unittest.mock import patch

import pytest

from src.api import routes
from src.api.core.models import DecisionRequest


@pytest.mark.parametrize("style", ["INTRADAY", "intraday", "SCALP"])
def test_decide_refuses_a_non_daily_style_before_any_fetch(style):
    with patch.object(routes, "fetch_all", side_effect=AssertionError("fetched for a refused style")):
        # routes.HTTPException: the class the code raises (another test in this suite stubs fastapi)
        with pytest.raises(routes.HTTPException) as e:
            asyncio.run(routes._decide("AAA", DecisionRequest(style=style)))
    assert e.value.status_code == 422 and e.value.detail["error"] == "unsupported_horizon"
