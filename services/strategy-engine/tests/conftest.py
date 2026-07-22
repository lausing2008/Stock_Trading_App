"""Real-load shared/common/indicators.py so src/dsl/evaluator.py's `from common.indicators
import atr as _canon_atr` (added by AUD-DUPLOGIC's ATR consolidation — evaluator.py used to
have its own inline ATR copy) resolves without needing the full common/db Docker-only stack —
evaluator.py itself has no other dependency on `common` beyond this one pure-computation import.
Matches the identical real-load pattern already established in market-data/ranking-engine/
signal-engine's own conftest.py files for this exact module.
"""
import importlib.util as _ilu
import pathlib as _pathlib
import sys

_indicators_path = _pathlib.Path(__file__).resolve().parents[3] / "shared" / "common" / "indicators.py"
_spec = _ilu.spec_from_file_location("common.indicators", _indicators_path)
_indicators_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_indicators_mod)

sys.modules.setdefault("common", _ilu.module_from_spec(_ilu.spec_from_loader("common", loader=None)))
sys.modules["common"].__path__ = []  # mark as a package so `from common.indicators import X` resolves
sys.modules["common.indicators"] = _indicators_mod
setattr(sys.modules["common"], "indicators", _indicators_mod)
