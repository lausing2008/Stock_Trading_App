from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.intel_reports import valuation_performance as P, valuation_outcome_store as O, valuation_jobs as J

PUBLISHED = datetime(2026, 10, 30, 5, tzinfo=timezone.utc)


def definition():
    return {"report_id": 55, "report_version": 4, "published_at": PUBLISHED.isoformat(),
            "groups": {"qualified": [], "undervalued_research": ["ADBE", "NEW"]}}


def test_entry_follows_publication_and_sessions_handle_dst_and_holidays():
    planned = P.plan(definition())
    assert planned[5]["entry_session"] == "2026-10-30"
    assert planned[5]["end_session"] == "2026-11-06"
    assert planned[5]["due_at"].astimezone(timezone.utc).hour == 21  # DST changed.
    same_close = {**definition(), "published_at": "2026-10-30T20:00:00Z"}
    assert P.plan(same_close)[5]["entry_session"] == "2026-11-02"


def test_future_horizons_do_not_use_prices_that_are_already_present_in_a_fixture():
    value = P.evaluate(definition(), {"ADBE": {"2026-10-30": 100, "2026-11-06": 500}}, now=PUBLISHED)
    assert value["horizons"][0]["groups"]["undervalued_research"]["state"] == "PENDING"
    assert value["price_observations"] == {}
    assert value["horizons"][0]["groups"]["qualified"]["state"] == "NO_COHORT"


def test_missing_or_delisted_members_block_aggregate_instead_of_improving_survivor_returns():
    end = P.plan(definition())[5]
    prices = {"ADBE": {end["entry_session"]: 100, end["end_session"]: 110},
              "SPY": {end["entry_session"]: 100, end["end_session"]: 105}}
    now = end["due_at"]
    partial = P.evaluate(definition(), prices, now=now)["horizons"][0]["groups"]["undervalued_research"]
    assert partial["observed"] == 1 and partial["expected"] == 2
    assert partial["state"] == "UNAVAILABLE" and partial["return_pct"] is None
    prices["NEW"] = {end["entry_session"]: 100, end["end_session"]: 90}
    complete = P.evaluate(definition(), prices, now=now)["horizons"][0]
    assert complete["groups"]["undervalued_research"]["return_pct"] == pytest.approx(0)
    assert complete["groups"]["undervalued_research"]["excess_percentage_points"] == pytest.approx(-5)
    prices["SPY"].pop(end["entry_session"])
    assert P.evaluate(definition(), prices, now=now)["horizons"][0]["groups"]["undervalued_research"]["state"] == "UNAVAILABLE"


def test_freeze_uses_later_information_cutoff_and_copies_source_membership():
    payload = {"fields": {"valuation": {"value": {"rows": [
        {"symbol": "ADBE", "classification": "undervalued", "qualified": False, "exclusions": []},
        {"symbol": "EXCLUDED", "classification": "undervalued", "qualified": True, "exclusions": ["illiquid"]}]}}}}
    report = SimpleNamespace(id=55, version=4, payload=payload, generated_at=PUBLISHED,
                             cutoff_at=PUBLISHED + timedelta(days=1))
    frozen = P.freeze(report)
    assert P.utc(frozen["published_at"]) == report.cutoff_at
    assert frozen["groups"]["undervalued_research"] == ["ADBE"]
    payload["fields"]["valuation"]["value"]["rows"].clear()
    assert frozen["groups"]["undervalued_research"] == ["ADBE"]


def test_outcome_storage_is_append_only_idempotent_and_preserves_the_cohort(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'outcomes.db'}")
    J.init_store(engine)
    factory = sessionmaker(bind=engine)
    report = SimpleNamespace(id=55, version=4, generated_at=PUBLISHED, cutoff_at=PUBLISHED,
        payload={"fields": {"valuation": {"value": {"rows": [
            {"symbol": "ADBE", "classification": "undervalued", "qualified": False, "exclusions": []}]}}}})
    O.register(factory, report, now=PUBLISHED)
    def no_prices(_): raise AssertionError("Future horizons must not fetch prices")
    O.refresh(factory, no_prices, now=PUBLISHED)
    first = deepcopy(O.latest(factory, 55))
    assert first["horizons"][0]["groups"]["undervalued_research"]["state"] == "PENDING"
    due = P.plan(P.freeze(report))[5]
    def prices(symbols):
        return {"source": "test adjusted closes", "series": {s: {"bars": [
            {"date": due["entry_session"], "adj_close": 100},
            {"date": due["end_session"], "adj_close": 110 if s == "ADBE" else 105}]} for s in symbols}}
    O.refresh(factory, prices, now=due["due_at"])
    second = O.latest(factory, 55)
    assert second["horizons"][0]["groups"]["undervalued_research"]["state"] == "OK"
    assert first["horizons"][0]["groups"]["undervalued_research"]["return_pct"] is None
    with factory() as session:
        assert len(session.execute(select(O.Outcome)).scalars().all()) == 2
        assert session.get(O.Cohort, 55).definition["groups"]["undervalued_research"] == ["ADBE"]
    report.payload["fields"]["valuation"]["value"]["rows"][0]["symbol"] = "CHANGED"
    with pytest.raises(ValueError): O.register(factory, report, now=PUBLISHED)
    engine.dispose()
