"""Regression test for T247-RESEARCHENGINE-CHECKLIST-INSTOWNERSHIP.

_build_checklist()'s "Institutional ownership > 50%?" item previously read
ai.get("institutional_pct"), a free-form LLM-guessed field with no scale/unit instruction in
the prompt — it could disagree with the real held_percent_institutions-derived value already
shown elsewhere in the same report on both source (guess vs. real data) and scale.
"""
from src.api.routes import _build_checklist


def _inst_item(checklist: dict) -> dict:
    for item in checklist["layer1_company"]:
        if item["item"] == "Institutional ownership > 50%?":
            return item
    raise AssertionError("checklist item not found")


def test_checklist_uses_real_fundamentals_when_ai_guess_disagrees():
    """The exact bug scenario: the AI guessed a low/wrong value, but the real
    held_percent_institutions says ownership is genuinely > 50%. The checklist must reflect
    the REAL data, not the AI's disagreeing guess."""
    ai = {"institutional_pct": 10}  # AI guessed low (or on the wrong 0-1 scale)
    raw_fund = {"held_percent_institutions": 0.75}  # real data: 75% institutional ownership

    checklist = _build_checklist({}, {}, ai, raw_fund=raw_fund)
    item = _inst_item(checklist)
    assert item["status"] == "pass"
    assert "75.0%" in item["note"]


def test_checklist_falls_back_to_ai_guess_when_no_real_fundamentals():
    """When raw_fund isn't available at all, fall back to the AI's own guess rather than
    showing nothing — matches the previous behavior for reports with no fundamentals data."""
    ai = {"institutional_pct": 62}
    checklist = _build_checklist({}, {}, ai, raw_fund=None)
    item = _inst_item(checklist)
    assert item["status"] == "pass"
    assert "62.0%" in item["note"]


def test_checklist_real_data_below_50_shows_warning():
    ai = {"institutional_pct": 90}  # AI guessed high — must NOT be trusted over real data
    raw_fund = {"held_percent_institutions": 0.30}
    checklist = _build_checklist({}, {}, ai, raw_fund=raw_fund)
    item = _inst_item(checklist)
    assert item["status"] == "warning"
    assert "30.0%" in item["note"]
