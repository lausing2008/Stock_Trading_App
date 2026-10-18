"""Real DB workflows, fixed clock, mocked provider boundary. Never uses a production database."""

import contextlib, importlib.util, io, json, os, pathlib, runpy
from datetime import datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parents[3]
test_db = os.environ.get("WORKSPACE_TEST_DB", "")
if test_db:
    assert (
        test_db.startswith("postgresql")
        and test_db.rsplit("/", 1)[-1] == "workflow_test"
    ), "Only a disposable workflow_test DB is allowed"
    os.environ["STOCKAI_INTEL_DB"] = test_db
else:
    os.environ.pop("STOCKAI_INTEL_DB", None)
# Use the established real-schema fixture setup without executing its unrelated scenarios.
p = ROOT / "services/research-engine/tests/_copilot_plan_probe.py"
g = {"__file__": str(p), "__name__": "workflow_fixture"}
exec(compile(p.read_text().rsplit("\nfor fn in ", 1)[0], str(p), "exec"), g)
spec = importlib.util.spec_from_file_location(
    "workspace_workflows",
    ROOT / "services/market-data/src/services/workspace_workflows.py",
)
W = importlib.util.module_from_spec(spec)
spec.loader.exec_module(W)
from db.models import (
    WorkspacePreference,
    DailyCloseAlert,
    AppNotification,
    ResearchRelationship,
    EventConnection,
    RealtimeNewsItem,
    Stock,
)
from sqlalchemy import select, func, inspect

S = g["Session"]
g["reset"]()
NOW = datetime(2026, 10, 9, 20, 30)  # Friday, NY regular close 20:00 UTC.
r = {}
with S() as s:
    for table in (
        EventConnection,
        ResearchRelationship,
        DailyCloseAlert,
        WorkspacePreference,
        AppNotification,
        RealtimeNewsItem,
    ):
        s.query(table).delete()
    s.commit()
layout = {
    "order": ["chart", "plan"],
    "hidden": {},
    "widths": {"chart": "wide"},
    "closed": {},
}
with S() as s:
    a = W.save_layout(s, 1, 0, layout)
    s.commit()
    b = W.save_layout(s, 1, 0, layout)
    c = W.save_layout(s, 1, 1, {**layout, "order": ["plan", "chart"]})
    s.commit()
    r["layout"] = {
        "created": a,
        "stale_create": b,
        "updated": c,
        "owner_layout": s.get(WorkspacePreference, 1).layout,
        "other_missing": s.get(WorkspacePreference, 2) is None,
    }
    try:
        W.validate_layout({**layout, "api_key": "do not persist"})
        r["extra_refused"] = False
    except ValueError:
        r["extra_refused"] = True
    alert = DailyCloseAlert(
        user_id=1,
        symbol="TESTCO",
        condition="below",
        threshold=101,
        origin="typed",
        request_id="close-test-0001",
        created_at=NOW - timedelta(hours=4),
    )
    s.add(alert)
    s.commit()
    aid = alert.id
    snapshot = {
        "session": "2026-10-09",
        "close": 100,
        "fetched_at": NOW.isoformat(),
        "provider": "scripted",
    }
    r["intraday"] = W.assess_close(alert, "US", NOW - timedelta(hours=2), snapshot)[
        "state"
    ]
    r["confirmed"] = W.assess_close(alert, "US", NOW, snapshot)["state"]
    r["stale_observation"] = W.assess_close(
        alert,
        "US",
        NOW,
        {**snapshot, "fetched_at": (NOW - timedelta(hours=1)).isoformat()},
    )["state"]
    r["missing"] = W.assess_close(alert, "US", NOW, None)["state"]
    r["weekend"] = W.completed_session("US", datetime(2026, 10, 10, 20))[0].isoformat()
    r["holiday"] = W.completed_session("US", datetime(2026, 12, 25, 23))[0].isoformat()
    r["hk_close"] = W.completed_session("HK", datetime(2026, 10, 9, 8, 30))[
        0
    ].isoformat()
    r["calendar_gap"] = W.completed_session("US", datetime(2030, 1, 4, 23))
W.check_close_alerts(S, lambda *_: snapshot, now=NOW)
W.check_close_alerts(S, lambda *_: snapshot, now=NOW)
with S() as s:
    r["one_notification"] = s.scalar(select(func.count()).select_from(AppNotification))
    r["deactivated"] = not s.get(DailyCloseAlert, aid).active
    edge = ResearchRelationship(
        source_symbol="TESTCO",
        target_symbol="OTHER",
        approved=False,
        payload={
            "valid_from": "2026-01-01",
            "valid_to": "2026-12-31",
            "keywords": ["capacity"],
            "evidence_url": "https://example.com/filing",
            "evidence_excerpt": "The filing describes this supplier relationship.",
            "relation": "supplier",
            "mechanism": "Demand may flow to the adjacent supplier.",
            "counterargument": "Inventory may absorb any incremental demand.",
            "falsifier": "The supplier loses the customer contract.",
            "horizon_days": 5,
            "direction": "watch",
        },
    )
    s.add(edge)
    s.flush()
    eid = edge.id
    s.add(
        RealtimeNewsItem(
            id=900,
            symbol="TESTCO",
            headline="Company expands capacity",
            url="https://example.com/event",
            source="test",
            published_at=NOW - timedelta(hours=1),
            ingested_at=NOW,
        )
    )
    s.commit()
    r["unapproved_no_candidates"] = W.propose_connections(s, NOW)
    edge.approved = True
    s.commit()
    r["approved_candidates"] = W.propose_connections(s, NOW)
    s.commit()
    r["duplicate_candidates"] = W.propose_connections(s, NOW)
    s.commit()
    card = s.scalar(select(EventConnection))
    r["pending"] = card.status
    r["has_falsifier"] = bool(card.payload["falsifier"])
    r["relationships_frozen"] = card.payload["relationship"]["evidence_url"]
    r["new_tables"] = [
        x
        for x in inspect(g["ENGINE"]).get_table_names()
        if x
        in (
            "daily_close_alerts",
            "event_connections",
            "workspace_preferences",
            "research_relationships",
        )
    ]
# Exercise the actual route functions with two database users and validated request bodies.
import importlib, importlib.util, sys
from db.models import User
from fastapi import HTTPException

alias = "workflow_md"
base = ROOT / "services/market-data/src"
spec = importlib.util.spec_from_file_location(
    alias, base / "__init__.py", submodule_search_locations=[str(base)]
)
pkg = importlib.util.module_from_spec(spec)
sys.modules[alias] = pkg
spec.loader.exec_module(pkg)
api = importlib.import_module(alias + ".api.workspace_workflows")
api.W = W
with S() as s:
    if s.get(User, 2) is None:
        s.add(User(id=2, username="bob", password_hash="x"))
    s.commit()
    alice = s.get(User, 1)
    bob = s.get(User, 2)
    body = api.CloseAlertIn(
        symbol="TESTCO", condition="below", threshold=95, request_id="route-close-0001"
    )
    first = api.close_create(body, user=alice, session=s)
    again = api.close_create(body, user=alice, session=s)
    r["route_idempotent"] = first["id"] == again["id"]
    r["route_other_empty"] = api.close_list(user=bob, session=s) == []
    try:
        api.close_delete(first["id"], user=bob, session=s)
        r["route_other_delete"] = False
    except HTTPException as exc:
        r["route_other_delete"] = exc.status_code == 404
    try:
        api.close_create(
            api.CloseAlertIn(**{**body.model_dump(), "threshold": 96}),
            user=alice,
            session=s,
        )
        r["route_key_conflict"] = False
    except HTTPException as exc:
        r["route_key_conflict"] = exc.status_code == 409
    api.close_delete(first["id"], user=alice, session=s)
    r["route_disabled_replay"] = not api.close_create(body, user=alice, session=s)[
        "active"
    ]
    r["unpublished_hidden"] = (
        api.cards(review_queue=False, symbol=None, user=bob, session=s) == []
    )
    try:
        api.cards(review_queue=True, symbol=None, user=bob, session=s)
        r["review_queue_guard"] = False
    except HTTPException as exc:
        r["review_queue_guard"] = exc.status_code == 403
# Publication freezes its baseline and outcome; later source revisions preserve old research.
from db.models import Price, TimeFrame, UserRole

original_clock = W.utcnow
W.utcnow = lambda: NOW
with S() as s:
    alice = s.get(User, 1)
    alice.role = UserRole.ADMIN
    s.commit()
    target = s.scalar(select(Stock).where(Stock.symbol == "OTHER"))
    s.add(
        Price(
            id=9001,
            stock_id=target.id,
            ts=NOW - timedelta(days=1),
            timeframe=TimeFrame.D1,
            open=50,
            high=51,
            low=49,
            close=50,
            volume=1000,
        )
    )
    s.add(
        Price(
            id=9002,
            stock_id=target.id,
            ts=NOW + timedelta(days=5) - timedelta(hours=1),
            timeframe=TimeFrame.D1,
            open=55,
            high=56,
            low=54,
            close=55,
            volume=1000,
        )
    )
    s.commit()
    card = s.scalar(select(EventConnection).order_by(EventConnection.id))
    cid = card.id
    published = api.card_review(cid, api.ReviewIn(approve=True), user=alice, session=s)
    r["frozen_baseline"] = published["baseline"]["close"]
    try:
        api.card_review(cid, api.ReviewIn(approve=True), user=alice, session=s)
        r["second_review_refused"] = False
    except HTTPException as exc:
        r["second_review_refused"] = exc.status_code == 409
    W.resolve_connections(s, NOW + timedelta(days=6))
    s.commit()
    r["prospective_return"] = s.get(EventConnection, cid).outcome[
        "raw_price_return_pct"
    ]
    news = s.get(RealtimeNewsItem, 900)
    news.headline = "Company revises capacity announcement"
    s.commit()
    W.propose_connections(s, NOW + timedelta(hours=1))
    s.commit()
    revised = s.scalar(
        select(EventConnection).where(EventConnection.status == "pending")
    )
    r["revision_links_previous"] = revised.payload["supersedes"] == cid
    W.utcnow = lambda: NOW + timedelta(hours=1)
    api.card_review(revised.id, api.ReviewIn(approve=True), user=alice, session=s)
    historical = api.card_history(cid, user=s.get(User, 2), session=s)
    r["history_preserved"] = (
        historical["status"] == "superseded" and historical["outcome"] is not None
    )
    # Publish past an intermediate pending revision; older research stays historical.
    prior_published = revised.id
    pending_ids = []
    for title in ("Capacity revision two", "Capacity revision three"):
        news.headline = title
        s.commit()
        W.propose_connections(s, NOW + timedelta(hours=2))
        s.commit()
        pending_ids.append(
            s.scalar(select(EventConnection.id).order_by(EventConnection.id.desc()))
        )
    api.card_review(pending_ids[-1], api.ReviewIn(approve=True), user=alice, session=s)
    r["skipped_revision_supersedes"] = (
        s.get(EventConnection, prior_published).status == "superseded"
    )
    try:
        api.card_review(
            pending_ids[0], api.ReviewIn(approve=True), user=alice, session=s
        )
        r["old_pending_refused"] = False
    except HTTPException as exc:
        r["old_pending_refused"] = exc.status_code == 409
W.utcnow = original_clock
if g["ENGINE"].dialect.name == "postgresql":
    from concurrent.futures import ThreadPoolExecutor

    def attempt(_):
        with S() as s:
            rev = W.save_layout(s, 1, 2, layout)
            s.commit()
            return rev

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(attempt, range(8)))
    r["postgres_conflict_winners"] = sum(x == 3 for x in outcomes)

    def evaluate(_):
        W.check_close_alerts(S, lambda *_: snapshot, now=NOW)

    with S() as s:
        s.get(DailyCloseAlert, aid).active = True
        s.query(AppNotification).delete()
        s.commit()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(evaluate, range(8)))
    with S() as s:
        r["postgres_notification_count"] = s.scalar(
            select(func.count()).select_from(AppNotification)
        )
    # The hundredth enrollment can be retried concurrently without hitting the cap.
    with S() as s:
        s.query(DailyCloseAlert).delete()
        s.add_all(
            [
                DailyCloseAlert(
                    user_id=1,
                    symbol="TESTCO",
                    condition="below",
                    threshold=95,
                    request_id=f"prefill-{i:04}",
                    origin="typed",
                    active=True,
                    created_at=NOW,
                )
                for i in range(99)
            ]
        )
        s.commit()

    def enroll(_):
        with S() as s:
            return api.close_create(body, user=s.get(User, 1), session=s)["id"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(enroll, range(8)))
    r["postgres_enrollment_ids"] = len(set(ids))
    assert r["postgres_conflict_winners"] == 1
    assert r["postgres_notification_count"] == 1
    assert r["postgres_enrollment_ids"] == 1
print(json.dumps(r))
