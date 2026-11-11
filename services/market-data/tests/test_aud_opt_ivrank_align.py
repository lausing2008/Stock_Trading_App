"""AUD-OPT-IVRANK-ALIGN (2026-11-10): backend and frontend use the same rich/cheap IV rank."""
import re
from pathlib import Path

from src.services import options_strategies as S

TS = Path(__file__).resolve().parents[3] / "frontend/src/lib/ivRank.ts"


def _ts_value(name: str) -> float:
    m = re.search(rf"export const {name} = ([0-9.]+);", TS.read_text())
    assert m, f"{name} not found in {TS}"
    return float(m.group(1))


def test_frontend_and_backend_thresholds_are_equal():
    assert _ts_value("IV_RANK_RICH") == S.IV_RANK_RICH
    assert _ts_value("IV_RANK_CHEAP") == S.IV_RANK_CHEAP


def test_the_call_finder_warns_from_the_shared_rich_threshold():
    from datetime import date
    from src.services.option_call_finder import find
    from tests.test_option_call_finder import SPOT, TODAY, chain
    warns = lambda r: find("near", chain(), spot=SPOT, today=TODAY, hold_days=14, iv_rank=r)["warnings"]
    assert any("IV rank 70" in w for w in warns(70.0))
    assert not any("IV rank" in w for w in warns(65.0))
