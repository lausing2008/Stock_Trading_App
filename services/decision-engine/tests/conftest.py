"""No Docker-only dependencies to stub — scorer.py and hard_rejects.py are pure/dependency-
light (only pydantic + stdlib datetime/zoneinfo, both real and installed). hard_rejects.py's
one DB dependency (SessionLocal, for the macro-blackout check) is only reached when
reasons["macro_blackout"] is None — tests avoid it by always providing that key explicitly.

AUD-DE-COMMONSTUB-NONPACKAGE (2026-09-09): the paragraph above is still true about what NEEDS
stubbing, but four test files in this directory stub `common` anyway — as a BARE MagicMock —
and that broke them all at collection time the moment hard_rejects.py started importing a real
`common` SUBMODULE.

    test_hard_rejects.py  test_score_replay.py  test_entry_gate_params.py  test_entry_weights.py
    -> ModuleNotFoundError: No module named 'common.market_calendar'; 'common' is not a package

`AUD-ENTRY-NYSEHOLIDAY-FOURTHCOPY` (commit 5796ebf, 2026-09-08) replaced hard_rejects.py's
private `_NYSE_HOLIDAYS` frozenset with `from common.market_calendar import NYSE_HOLIDAYS`. That
consolidation was correct and is not being reverted — but a bare `MagicMock()` in sys.modules is
not a PACKAGE, so Python refuses to resolve any `common.<submodule>` import through it, and the
four files above stopped collecting entirely. They failed silently: the suite reported
"4 errors during collection" rather than a test failure, so a green-looking targeted run could
still hide four whole files being skipped.

THE FIX, borrowed verbatim from market-data/tests/conftest.py, which already solved this for the
same module: load the real `common.market_calendar` from `shared/` and register it BOTH in
sys.modules AND as an attribute on whatever `common` object is present.

A CORRECTION TO MY OWN FIRST EXPLANATION: I claimed both halves were REQUIRED, and that
sys.modules alone would not satisfy `from common.market_calendar import X`. That is false —
verified directly: with only the sys.modules entry and no setattr, the from-import succeeds and
returns the real frozenset. CPython resolves `from X.Y import Z` against sys.modules["X.Y"]
first. The setattr is kept as belt-and-braces (it makes `common.market_calendar` reachable as a
plain attribute, which some code and some debuggers expect) but it is NOT load-bearing, and no
test should assert that it is.

It also matters that the calendar is REAL rather than mocked: it carries actual holiday DATA and
every guard on it is a membership test, so under a blanket mock `date(...) in NYSE_HOLIDAYS`
returns a truthy Mock and every holiday test passes vacuously. Pure stdlib, nothing to stub.

This runs at conftest import — before any test module — so it is in place regardless of whether
an individual file adds its own `common` stub afterwards (setdefault won't overwrite it).
"""
import sys
from unittest.mock import MagicMock

import importlib.util as _ilu
import pathlib as _pathlib

# Ensure a `common` parent exists to hang the real submodule off. If a test file later calls
# sys.modules.setdefault("common", MagicMock()), it is a no-op — this one wins.
if "common" not in sys.modules:
    sys.modules["common"] = MagicMock()

# The MOCKED submodules the decision-engine core imports. These are stubbed rather than loaded
# for real because they reach outside the process (env/settings, a Redis connection) — unlike
# market_calendar below, which is pure data. The sys.modules entry is what makes
# `from common.config import get_settings` resolve at all; the setattr is belt-and-braces (see
# the correction in the module docstring above).
for _name in ("config", "redis_client", "jwt_auth", "ai_keys"):
    _full = f"common.{_name}"
    if _full not in sys.modules:
        sys.modules[_full] = MagicMock()
    setattr(sys.modules["common"], _name, sys.modules[_full])

_calendar_path = (
    _pathlib.Path(__file__).resolve().parents[3] / "shared" / "common" / "market_calendar.py"
)
if _calendar_path.exists():
    _cal_spec = _ilu.spec_from_file_location("common.market_calendar", _calendar_path)
    _calendar_mod = _ilu.module_from_spec(_cal_spec)
    _cal_spec.loader.exec_module(_calendar_mod)
    sys.modules["common.market_calendar"] = _calendar_mod
    setattr(sys.modules["common"], "market_calendar", _calendar_mod)
