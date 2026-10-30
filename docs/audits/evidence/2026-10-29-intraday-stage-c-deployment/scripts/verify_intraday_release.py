"""Post-deploy checks for Intraday Stage C (a3107b47). Run inside stockai-market-data-1:
    docker exec -i -w /app stockai-market-data-1 python - < verify_intraday_release.py

Read-only against business data. Mints one admin and one non-admin JWT in-process, never prints
them, and revokes both through /auth/logout at the end (jti blacklist). Prints one JSON report.
"""
import json
import re
from datetime import date

import httpx
from sqlalchemy import text

from db.session import engine
from src.api.auth import _make_token
from src.services.intraday import models as M
from src.services.intraday import schema as S

GW = "http://api-gateway:8000"
report = {"checks": {}}
failed = []


def check(name, ok, detail):
    report["checks"][name] = {"ok": bool(ok), "detail": detail}
    if not ok:
        failed.append(name)


with engine.connect() as conn:
    tables = sorted(t.name for t in M.metadata.sorted_tables)
    present = {r[0] for r in conn.execute(text(
        "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"))}
    check("tables_present", set(tables) <= present, {"expected": tables, "missing": sorted(set(tables) - present)})

    want_cons = sorted({c.name for t in M.metadata.sorted_tables for c in t.constraints
                        if c.name and not c.name.startswith("_unnamed")})
    have_cons = {r[0] for r in conn.execute(text(
        "SELECT conname FROM pg_constraint c JOIN pg_class r ON r.oid = c.conrelid "
        "WHERE r.relname = ANY(:t)"), {"t": tables})}
    check("named_constraints_present", set(want_cons) <= have_cons,
          {"expected_count": len(want_cons), "missing": sorted(set(want_cons) - have_cons)})

    want_trg = sorted({m.group(1) for s in S._pg_triggers()
                       for m in [re.match(r"CREATE (?:CONSTRAINT )?TRIGGER (\w+)", s)] if m})
    have_trg = {r[0]: r[1] for r in conn.execute(text(
        "SELECT t.tgname, t.tgenabled FROM pg_trigger t JOIN pg_class r ON r.oid = t.tgrelid "
        "WHERE NOT t.tgisinternal AND r.relname = ANY(:t)"), {"t": tables})}
    missing_trg = sorted(set(want_trg) - set(have_trg))
    disabled_trg = sorted(n for n in want_trg if have_trg.get(n) == "D")
    check("triggers_present_enabled", not missing_trg and not disabled_trg,
          {"expected_count": len(want_trg), "missing": missing_trg, "disabled": disabled_trg})

    want_fn = sorted({m.group(1) for s in S._PG_FUNCTIONS
                      for m in [re.search(r"FUNCTION (\w+)\(", s)] if m})
    have_fn = {r[0] for r in conn.execute(text("SELECT proname FROM pg_proc WHERE proname = ANY(:f)"), {"f": want_fn})}
    check("functions_present", set(want_fn) <= have_fn, {"expected": want_fn, "missing": sorted(set(want_fn) - have_fn)})

    # The trigger carries the ICR-01 rules: confirm the deployed body is the reviewed one.
    body = conn.execute(text("SELECT prosrc FROM pg_proc WHERE proname = 'intraday_validate_append'")).scalar() or ""
    check("append_trigger_has_icr01_rules", "replay_latency_s" in body and "intraday_timing:" in body,
          {"has_replay_latency": "replay_latency_s" in body, "has_timing_prefix": "intraday_timing:" in body})

    runs = [dict(r._mapping) for r in conn.execute(text("SELECT run_key, record_mode FROM intraday_runs"))]
    check("observed_sentinel_only", runs == [{"run_key": "observed", "record_mode": "observed"}], {"runs": runs})

    counts = {t: conn.execute(text(f'SELECT count(*) FROM "{t}"')).scalar() for t in tables if t != "intraday_runs"}
    check("business_tables_empty", all(v == 0 for v in counts.values()), counts)

    enum_vals = [r[0] for r in conn.execute(text(
        "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
        "WHERE t.typname = 'signalhorizon' ORDER BY e.enumsortorder"))]
    check("signalhorizon_enum_unchanged", "INTRADAY" not in enum_vals and len(enum_vals) > 0, {"labels": enum_vals})

    admin = conn.execute(text("SELECT username, role, tier FROM users WHERE role = 'ADMIN' "
                              "AND is_active ORDER BY id LIMIT 1")).first()
    user = conn.execute(text("SELECT username, role, tier FROM users WHERE role = 'USER' "
                             "AND is_active ORDER BY id LIMIT 1")).first()

report["schema_status_in_this_process"] = S.SCHEMA_STATUS  # None here: a fresh interpreter, not the server
tok_admin = _make_token(admin.username, admin.role, admin.tier)
tok_user = _make_token(user.username, user.role, user.tier) if user else None
H = lambda t: {"Authorization": f"Bearer {t}"}  # noqa: E731
today = date.today().isoformat()
q = f"/stocks/intraday/identities?session={today}&mode=observed"

with httpx.Client(base_url=GW, timeout=60) as c:
    r = c.get(q)
    check("intraday_unauthenticated_401", r.status_code == 401, {"status": r.status_code})
    if tok_user:
        r = c.get(q, headers=H(tok_user))
        check("intraday_non_admin_403", r.status_code == 403, {"status": r.status_code})
    r = c.get(q, headers=H(tok_admin))
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text[:300]
    check("intraday_admin_200_disabled_empty",
          r.status_code == 200 and isinstance(body, dict) and body.get("production_generation") == "disabled",
          {"status": r.status_code, "body": body})
    r = c.get(f"/stocks/intraday/identities?session={today}&mode=observed&market=HK", headers=H(tok_admin))
    check("intraday_hk_422", r.status_code == 422, {"status": r.status_code, "body": r.text[:200]})

    for path in ("/signals?style=INTRADAY", "/signals/suppressed?style=INTRADAY",
                 "/signals/AAPL?style=INTRADAY&persist=false&live=false"):
        r = c.get(path, headers=H(tok_admin))
        err = (r.json().get("detail") or {}).get("error") if r.status_code == 422 else None
        check(f"daily_rejects_intraday {path}", r.status_code == 422 and err == "unsupported_horizon",
              {"status": r.status_code, "error": err})
    r = c.post("/decide/AAPL", headers=H(tok_admin), json={"style": "INTRADAY"})
    det = r.json().get("detail") if r.status_code == 422 else None
    check("decide_rejects_intraday",  # a pydantic 422 (list detail) would NOT be the horizon guard
          isinstance(det, dict) and det.get("error") == "unsupported_horizon", {"status": r.status_code, "detail": det})

    r = c.get("/signals?style=SWING", headers=H(tok_admin))
    n = len(r.json()) if r.status_code == 200 and isinstance(r.json(), list) else None
    check("daily_swing_signals_200", r.status_code == 200, {"status": r.status_code, "rows": n})

    revoked = {}
    for name, t in (("admin", tok_admin), ("user", tok_user)):
        if t:
            lo = c.post("/auth/logout", headers=H(t))
            after = c.get("/signals?style=SWING", headers=H(t))
            revoked[name] = {"logout": lo.status_code, "reuse_after_logout": after.status_code}
    check("test_tokens_revoked", all(v["reuse_after_logout"] == 401 for v in revoked.values()), revoked)

report["failed"] = failed
report["ok"] = not failed
print(json.dumps(report, indent=1, sort_keys=True, default=str))
