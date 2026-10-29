"""Intraday Stage C mutation run: each required guard is disabled in turn and its OWN witness is
run. A case is CAUGHT only when the witness fails, bounded in time, with test failures (pytest
exit 1, no collection/usage errors) AND the expected witness test among the failures. A timeout
is INCONCLUSIVE, never a catch. Anything else is MISSED or WRONG_REASON.

Usage (from a clean checkout of the commit under test):
  INTRADAY_PG_URL=postgresql+psycopg2://...disposable... python scripts/intraday_mutation_run.py OUT_DIR
Writes OUT_DIR/results.json (per case: file and mutation hashes, witness command, failing tests,
verdict, output hash) and OUT_DIR/outputs/<case>.txt. Every mutated file is restored from an
in-memory copy and verified by hash after each case.

Bytecode: every __pycache__ under the checkout is purged first and every witness runs with
PYTHONDONTWRITEBYTECODE=1, so no compiled file can outlive its source. (Found 2026-10-29: a
same-length mutation restored within the same second left a .pyc that Python still trusted --
its validation is mtime-seconds + size -- so later runs executed the mutated code.)
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(sys.argv[1])
PG = os.environ.get("INTRADAY_PG_URL", "")
if not PG.startswith("postgresql"):
    sys.exit("INTRADAY_PG_URL must name a disposable PostgreSQL 16 database")
SE, MD, ML = "services/signal-engine", "services/market-data", "services/ml-prediction"
PYQ = [sys.executable, "-m", "pytest", "-q", "-rf", "-p", "no:cacheprovider", "-W", "ignore"]


def iso(service, suite, k, url="sqlite://"):
    return f"{service}/tests", PYQ + ["--noconftest", suite, "-k", k], {"INTRADAY_SUITE_URL": url}


def unit(service, test_file, k):
    return service, PYQ + [test_file, "-k", k], {}


SE_ISO = lambda k: iso(SE, "intraday_isolation_suite.py", k)          # noqa: E731
STORE = lambda k, url=PG: iso(MD, "intraday_store_suite.py", k, url)   # noqa: E731
INPUTS = lambda k: iso(MD, "intraday_inputs_suite.py", k, PG)          # noqa: E731
CONTRACT = lambda k: unit(MD, "tests/test_intraday_contract.py", k)   # noqa: E731
MD_ISO = lambda k: unit(MD, "tests/test_intraday_daily_isolation.py", k)  # noqa: E731

# (id, file, old, new, witness, expected failing test substring)
CASES = [
    ("registry", "shared/common/horizons.py", "    if h not in DAILY_HORIZONS:\n        raise UnsupportedHorizon(value)",
     "    if False:\n        raise UnsupportedHorizon(value)", SE_ISO("422"), "unknown_or_intraday_style_is_422"),
    ("se_all_latest_style", f"{SE}/src/api/routes.py", "    horizon_filter = _daily_style_or_422(style) if style else None",
     "    horizon_filter = style.upper() if style else None", SE_ISO("422"), "unknown_or_intraday_style_is_422"),
    ("se_signal_for_style", f"{SE}/src/api/routes.py", "    if style:\n        style = _daily_style_or_422(style)\n", "",
     SE_ISO("422"), "unknown_or_intraday_style_is_422"),
    ("se_upsert_hor", f"{SE}/src/api/routes.py", "                            hor=parse_daily_horizon(ai.horizon),",
     "                            hor=ai.horizon,", SE_ISO("upserts"), "both_signal_upserts"),
    ("se_outcome_hold_days", f"{SE}/src/api/outcomes.py", "                hold_days = _hold_days_for(sig.horizon.value, sig_dir)",
     "                hold_days = _OUTCOME_HOLD_DAYS[sig.horizon.value]", SE_ISO("outcome_loops"), "outcome_loops_route_hold_days"),
    ("se_outcome_pending_filter", f"{SE}/src/api/outcomes.py",
     "            Signal.horizon.in_([SignalHorizon(h) for h in DAILY_HORIZONS]),\n", "", SE_ISO("pending"), "outcome_evaluator_pending"),
    ("se_cal_ta", f"{SE}/src/api/calibration.py",
     "            Signal.reasons.is_not(None),\n            SignalOutcome.horizon.in_(_DAILY_ENUMS),\n        )\n        # Chronological order is load-bearing: TimeSeriesSplit",
     "            Signal.reasons.is_not(None),\n        )\n        # Chronological order is load-bearing: TimeSeriesSplit",
     SE_ISO("calibrate_ta_weights"), "calibrate_ta_weights"),
    ("se_cal_conviction", f"{SE}/src/api/calibration.py",
     "            Signal.reasons.is_not(None),\n            SignalOutcome.horizon.in_(_DAILY_ENUMS),\n        )\n        # Chronological order is load-bearing: the train/validation",
     "            Signal.reasons.is_not(None),\n        )\n        # Chronological order is load-bearing: the train/validation",
     SE_ISO("calibrate_conviction_weights"), "calibrate_conviction_weights"),
    ("se_cal_outcomes", f"{SE}/src/api/calibration.py",
     "            SignalOutcome.signal_direction == \"BUY\",\n            SignalOutcome.horizon.in_(_DAILY_ENUMS),\n",
     "            SignalOutcome.signal_direction == \"BUY\",\n", SE_ISO("outcomes_calibration"), "outcomes_calibration"),
    ("se_accuracy", f"{SE}/src/api/analytics.py", "        .where(Signal.signal.in_([SignalType.BUY, SignalType.SELL]))\n        .where(Signal.horizon.in_(_DAILY_ENUMS))\n",
     "        .where(Signal.signal.in_([SignalType.BUY, SignalType.SELL]))\n", SE_ISO("signal_accuracy"), "signal_accuracy"),
    ("se_rolling", f"{SE}/src/api/analytics.py",
     "            Signal.signal == SignalType.BUY,\n            Signal.horizon.in_(_DAILY_ENUMS),\n        )\n        .order_by(Signal.ts.asc())\n    ).all()\n\n    if not rows:\n        return {\"window\"",
     "            Signal.signal == SignalType.BUY,\n        )\n        .order_by(Signal.ts.asc())\n    ).all()\n\n    if not rows:\n        return {\"window\"",
     SE_ISO("rolling_accuracy"), "rolling_accuracy"),
    ("se_factor", f"{SE}/src/api/analytics.py", "        .where(Signal.signal == SignalType.BUY)\n        .where(Signal.horizon.in_(_DAILY_ENUMS))\n",
     "        .where(Signal.signal == SignalType.BUY)\n", SE_ISO("factor_exposure"), "factor_exposure"),
    ("se_walkforward", f"{SE}/src/api/analytics.py", "            Stock.active.is_(True),\n            Signal.horizon.in_(_DAILY_ENUMS),\n",
     "            Stock.active.is_(True),\n", SE_ISO("walkforward"), "walkforward"),
    ("se_summary_scored", f"{SE}/src/api/analytics.py",
     "        SignalOutcome.is_correct.is_not(None),\n        SignalOutcome.horizon.in_(_DAILY_ENUMS),\n    )",
     "        SignalOutcome.is_correct.is_not(None),\n    )", SE_ISO("outcomes_summary"), "outcomes_summary"),
    ("se_summary_censored", f"{SE}/src/api/analytics.py",
     "        SignalOutcome.skip_reason == \"no_exit_price\",\n        SignalOutcome.horizon.in_(_DAILY_ENUMS),\n",
     "        SignalOutcome.skip_reason == \"no_exit_price\",\n", SE_ISO("outcomes_summary"), "outcomes_summary"),
    ("se_consensus", f"{SE}/src/api/routes.py", "        .where(Stock.active.is_(True), Signal.horizon.in_(_DAILY_ENUMS))",
     "        .where(Stock.active.is_(True))", SE_ISO("consensus"), "signal_consensus"),
    ("se_recent_changes", f"{SE}/src/api/routes.py", "            Signal.ts >= cutoff,\n            Signal.horizon.in_(_DAILY_ENUMS),\n",
     "            Signal.ts >= cutoff,\n", SE_ISO("recent_changes"), "recent_changes"),
    ("se_calibration_store", f"{SE}/src/calibration_store.py",
     ",\n               Outcome.horizon.in_([SignalHorizon(h) for h in DAILY_HORIZONS]))", ")",
     SE_ISO("calibration_store"), "calibration_store"),
    ("ml_meta_trainer", f"{ML}/src/training/meta_trainer.py", "              AND CAST(so.horizon AS text) = ANY(:daily_horizons)\n", "",
     unit(ML, "tests/test_intraday_daily_isolation.py", "meta_trainer"), "meta_trainer_reads_daily"),
    ("ml_route_fallback", f"{ML}/src/api/routes.py", "    return _daily_horizon_days(req.style)",
     "    return _HORIZON_BY_STYLE.get(req.style.upper(), req.horizon)",
     unit(ML, "tests/test_intraday_daily_isolation.py", "route"), "no_route_defaults"),
    ("ei_catalyst", "services/event-intelligence/src/scheduler.py",
     "                \"AND CAST(horizon AS text) = ANY(:daily_horizons) \"  # Intraday Stage C §10\n", "",
     unit("services/event-intelligence", "tests/test_intraday_daily_isolation.py", "catalyst"), "catalyst_recompute"),
    ("md_paper_rsi", f"{MD}/src/services/paper_trading_engine.py",
     "                    \"AND CAST(sig.horizon AS text) = ANY(:daily_horizons) \"  # Intraday Stage C §10\n", "",
     MD_ISO("paper_exit"), "paper_exit_rsi"),
    ("md_dq_freshness", f"{MD}/src/services/scheduler.py",
     "WHERE st.market='HK'\"\n                 \" AND CAST(sig.horizon AS text) IN \" + str(tuple(_DAILY_HORIZONS)),", "WHERE st.market='HK'\",",
     MD_ISO("freshness"), "signal_freshness"),
    ("md_curation_skip", f"{MD}/src/services/scheduler.py", "                if not _is_daily_horizon(style):", "                if False:",
     MD_ISO("curation"), "watchlist_curation"),
    ("md_watchlist_style", f"{MD}/src/api/watchlist.py", "        return parse_daily_horizon(value)", "        return value.upper()",
     MD_ISO("watchlist_style"), "watchlist_style_must"),
    ("md_router_order", f"{MD}/src/main.py", "routers=[rzy_router, intraday_router, ", "routers=[rzy_router, ",
     MD_ISO("router"), "router_registers_before"),
    ("re_report_signal", "services/research-engine/src/intel_reports/adapters.py",
     ",\n                             Signal.horizon.in_([SignalHorizon(h) for h in DAILY_HORIZONS]))  # Intraday Stage C §10", ")",
     unit("services/research-engine", "tests/test_intraday_daily_isolation.py", "latest_signal"), "latest_signal_reads_daily"),
    ("de_decide_style", "services/decision-engine/src/api/routes.py", "        style = parse_daily_horizon(req.style)",
     "        style = req.style.upper()", unit("services/decision-engine", "tests/test_intraday_daily_isolation.py", "decide"),
     "decide_refuses"),
    # contract and research fills
    ("c_strict_open", f"{MD}/src/services/intraday/contract.py", "        if after < t < before:", "        if after <= t < before:",
     CONTRACT("exactly_on_a_bar_open"), "exactly_on_a_bar_open"),
    ("c_expiry_exclusive", f"{MD}/src/services/intraday/contract.py", "    if detected_at >= expires:", "    if detected_at > expires:",
     CONTRACT("expiry"), "before_at_and_after_expiry"),
    ("c_cancel_order", f"{MD}/src/services/intraday/contract.py", "    return \"cancelled\" if ce < eo else \"entered\"",
     "    return \"cancelled\" if ce > eo else \"entered\"", CONTRACT("cancellation_timing"), "cancellation_timing_against_entry"),
    ("c_tie_ambiguous", f"{MD}/src/services/intraday/contract.py", "    if ce == eo:\n        return \"chronology_ambiguous\"\n", "",
     CONTRACT("exactly_at_the_entry_open"), "exactly_at_the_entry_open"),
    ("c_observed_clock", f"{MD}/src/services/intraday/contract.py", "        return utc(ev.decided_at)",
     "        return utc(ev.eligible_at or ev.decided_at)", CONTRACT("not_backdated"), "not_backdated"),
    ("f_stop_wins", f"{MD}/src/services/intraday/fills.py", "        if b.low <= stop:                       # double touch: the stop wins",
     "        if b.low <= stop and not b.high > target:", CONTRACT("double_touch"), "double_touch"),
    ("f_trade_through", f"{MD}/src/services/intraday/fills.py", "        if b.high > target:", "        if b.high >= target:",
     CONTRACT("trade_through"), "trade_through"),
    ("f_bracket_reject", f"{MD}/src/services/intraday/fills.py", "    if fill <= stop or fill >= target:", "    if False:",
     CONTRACT("gap_through_the_stop_at_entry"), "gap_through_the_stop_at_entry"),
    ("f_indeterminate", f"{MD}/src/services/intraday/fills.py", "        if b is None:\n            # A held interval with no bar",
     "        if b is None and False:\n            # A held interval with no bar", CONTRACT("never_yields"), "never_yields_a_confirmed"),
    ("f_stop_excursion", f"{MD}/src/services/intraday/fills.py",
     "            return _exit(res, t, stop * (1 - slip), \"stop\", \"unknown\", risk, fill, lo, hi, params, bounds)",
     "            return _exit(res, t, stop * (1 - slip), \"stop\", \"unknown\", risk, fill, min(lo, b.low), max(hi, b.high), params, bounds)",
     CONTRACT("excursions_exclude"), "excursions_exclude"),
    ("f_target_excursion", f"{MD}/src/services/intraday/fills.py",
     "            return _exit(res, t, target, \"target\", \"unknown\", risk, fill, lo, hi, params, bounds)",
     "            return _exit(res, t, target, \"target\", \"unknown\", risk, fill, lo, max(hi, b.high), params, bounds)",
     CONTRACT("post_exit_extreme"), "post_exit_extreme"),
    # storage, PostgreSQL
    ("s_append_seq", f"{MD}/src/services/intraday/schema.py",
     "         ELSIF NOT (NEW.seq = last_seq + 1 AND NEW.from_state IS NOT DISTINCT FROM last_state) THEN",
     "         ELSIF false THEN", STORE("skips_the_writer"), "skips_the_writer"),
    ("s_deny_update", f"{MD}/src/services/intraday/schema.py",
     "       BEGIN RAISE EXCEPTION 'intraday_insert_only: UPDATE of % refused', TG_TABLE_NAME; END $$",
     "       BEGIN RETURN NEW; END $$", STORE("direct_update"), "direct_update"),
    ("s_guard_delete", f"{MD}/src/services/intraday/schema.py",
     "         IF coalesce(current_setting('stockai.intraday_retention', true), '') <> 'on' THEN", "         IF false THEN",
     STORE("direct_update"), "direct_update"),
    ("s_seal_check", f"{MD}/src/services/intraday/schema.py",
     "                    AND s.identity_hash = NEW.identity_hash AND s.session_date = NEW.session_date) THEN",
     "                    AND s.identity_hash = NEW.identity_hash AND s.session_date = NEW.session_date AND false) THEN",
     STORE("late_evaluation_into_a_sealed"), "late_evaluation_into_a_sealed"),
    ("s_trigger_session_lock", f"{MD}/src/services/intraday/schema.py",
     "         PERFORM intraday_session_lock(NEW.run_key, NEW.identity_hash, NEW.session_date);\n", "",
     STORE("behind_an_open_seal"), "behind_an_open_seal"),
    ("s_sealer_session_lock", f"{MD}/src/services/intraday/store.py",
     "    and every insert after it sees the seal (review IC-01).\"\"\"\n    _session_lock(conn, run_key, identity, session_date)\n",
     "    and every insert after it sees the seal (review IC-01).\"\"\"\n", STORE("in_flight"), "in_flight_insert"),
    ("s_read_committed", f"{MD}/src/services/intraday/schema.py",
     "         IF current_setting('transaction_isolation') <> 'read committed' THEN", "         IF false THEN",
     STORE("read_committed"), "read_committed"),
    ("s_predecessor_fk", f"{MD}/src/services/intraday/models.py",
     "    ForeignKeyConstraint([\"supersedes_id\", \"setup_id\", \"kind\", \"window\", \"run_key\"],\n"
     "                         [\"intraday_outcomes.id\", \"intraday_outcomes.setup_id\", \"intraday_outcomes.kind\",\n"
     "                          \"intraday_outcomes.window\", \"intraday_outcomes.run_key\"],\n"
     "                         name=\"fk_intraday_outcome_predecessor\"),\n", "", STORE("cross_chain"), "cross_chain"),
    ("s_one_reconciled_index", f"{MD}/src/services/intraday/models.py",
     "Index(\"uq_intraday_one_reconciled\", transitions.c.setup_id, unique=True,",
     "Index(\"uq_intraday_one_reconciled\", transitions.c.setup_id, transitions.c.seq, unique=True,",
     STORE("partial_indexes"), "partial_indexes"),
    ("s_archive_retry", f"{MD}/src/services/intraday/store.py", "    if not stored:\n", "    if True:\n",
     STORE("archive_is_atomic"), "archive_is_atomic"),
    ("s_clock_mode_transition", f"{MD}/src/services/intraday/models.py",
     "    CheckConstraint(\"(run_key = 'observed') = (clock_basis = 'observed')\", name=\"ck_intraday_transition_clock_mode\"),\n", "",
     STORE("direct_sql_cannot_write_cross_mode"), "direct_sql_cannot_write_cross_mode"),
    ("s_clock_mode_input", f"{MD}/src/services/intraday/models.py",
     "    CheckConstraint(\"(run_key = 'observed') = (read_clock_basis = 'observed')\", name=\"ck_intraday_input_clock_mode\"),\n", "",
     STORE("direct_sql_cannot_write_cross_mode"), "direct_sql_cannot_write_cross_mode"),
    ("s_effective_before_decided", f"{MD}/src/services/intraday/models.py",
     "    CheckConstraint(\"effective_at <= decided_at\", name=\"ck_intraday_transition_effective_before_decided\"),\n", "",
     STORE("direct_sql_cannot_write_cross_mode"), "direct_sql_cannot_write_cross_mode"),
    ("s_chronology_trigger", f"{MD}/src/services/intraday/schema.py",
     "         ELSIF NEW.effective_at < last_eff OR NEW.decided_at < last_dec THEN", "         ELSIF false THEN",
     STORE("exit_before_its_entry"), "exit_before_its_entry"),
    ("s_entry_window_trigger", f"{MD}/src/services/intraday/schema.py", "         ELSIF NEW.to_state = 'entered' AND NOT",
     "         ELSIF false AND NOT", STORE("direct_sql_cannot_write_cross_mode"), "direct_sql_cannot_write_cross_mode"),
    ("w_clock_mode", f"{MD}/src/services/intraday/store.py", "    if event.clock_basis != _basis_for(setup.run_key):", "    if False:",
     STORE("cross_mode_inputs", "sqlite://"), "cross_mode_inputs_and_events"),
    ("w_entry_window", f"{MD}/src/services/intraday/store.py",
     "    if to_state == \"entered\" and not (setup.actionable_at is not None",
     "    if False and not (setup.actionable_at is not None", STORE("after_its_expiry", "sqlite://"), "entry_after_its_expiry"),
    ("w_idempotency_recheck", f"{MD}/src/services/intraday/store.py",
     "    setup = _lock_setup(conn, setup_id)\n    if (d := _done()) is not None:\n        return d\n",
     "    setup = _lock_setup(conn, setup_id)\n", STORE("concurrent_identical"), "concurrent_identical"),
    ("w_evidence_verify", f"{MD}/src/services/intraday/store.py", "        if c.hashes_from_evidence(i.evidence) != stated:",
     "        if False:", STORE("tampered_evidence", "sqlite://"), "tampered_evidence"),
    ("i_evidence_persisted", f"{MD}/src/services/intraday/inputs.py",
     "            evidence=ev, extra={\"provider\": provider or \"unknown\", \"provider_basis\": basis}))",
     "            evidence={\"version\": 1, \"raw\": {}, \"transformed\": {}}, extra={\"provider\": provider or \"unknown\", \"provider_basis\": basis}))",
     INPUTS("recompute"), "recompute_every_hash"),
    ("i_provider_identity", f"{MD}/src/services/intraday/inputs.py",
     "        provider = p.provider if consistent and not trace_failed else None",
     "        provider = p.provider if p is not None else None", INPUTS("failed_trace"), "failed_trace"),
    ("r_mode_rules", f"{MD}/src/services/intraday/read.py",
     "        if run_key:\n            raise BadRequest(\"mode=observed takes no run_key\")\n", "", STORE("mode_and_run"), "mode_and_run"),
    ("r_identity_required", f"{MD}/src/services/intraday/read.py",
     "    if not identity:\n        raise BadRequest(\"identity is required; see /identities\")\n    ident = _identity_row(conn, identity)\n    where = [setups.c.run_key == rk, setups.c.identity_hash == identity,",
     "    ident = _identity_row(conn, identity) if identity else {}\n    where = [setups.c.run_key == rk,",
     STORE("never_pooled"), "never_pooled"),
    ("r_open_exposure", f"{MD}/src/services/intraday/read.py", "where(latest_q.c.to_state.in_(c.OPEN_EXPOSURE))",
     "where(latest_q.c.to_state == \"unresolved_exit_at_close\")", STORE("indeterminate_is_listed"), "indeterminate_is_listed"),
    ("r_archived_identity", f"{MD}/src/services/intraday/read.py",
     "        found[h] = dict(_identity_row(conn, h), evaluations=int(n))       # archive is the full count\n", "",
     STORE("archived_identity"), "archived_identity"),
]

NOT_REQUIRED = [
    {"id": "se_signal_for_swing_fallback", "file": f"{SE}/src/api/routes.py", "line": "ai = all_sig[style_key]",
     "reason": "unreachable: the style is validated at the top of signal_for (case se_signal_for_style), so the old "
               "SWING fallback cannot be hit; not an independent guard"},
    {"id": "f_end_of_loop", "file": f"{MD}/src/services/intraday/fills.py",
     "line": "raise ContractViolation(\"holding loop ended without an exit or an unobserved interval\")",
     "reason": "unreachable in research fill model v1: a fully observed session reaches the liquidation open and "
               "any missing bar returns path_indeterminate first; the line refuses rather than invents an outcome"},
]


def sha(b):
    return hashlib.sha256(b if isinstance(b, bytes) else b.encode()).hexdigest()


def git_head():
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def purge_bytecode():
    for d in ROOT.rglob("__pycache__"):
        if ".git" not in d.parts:
            for f in d.iterdir():
                f.unlink()
            d.rmdir()
    left = [str(p) for p in ROOT.rglob("*.pyc") if ".git" not in p.parts]
    if left:
        sys.exit(f"refusing: compiled files remain after purge: {left[:3]}")


def main():
    if subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, capture_output=True,
                      text=True).stdout.strip():
        sys.exit("refusing: the checkout has uncommitted changes; run against an exact commit")
    head = git_head()
    purge_bytecode()
    (OUT / "outputs").mkdir(parents=True, exist_ok=True)
    results = []
    for cid, rel, old, new, (cwd, cmd, env), expect in CASES:
        path = ROOT / rel
        original = path.read_bytes()
        text = original.decode()
        rec = {"id": cid, "file": rel, "file_sha256": sha(original), "expected_witness": expect,
               "witness_cmd": " ".join(cmd[1:]) if cmd[0] == sys.executable else " ".join(cmd), "cwd": cwd}
        if text.count(old) != 1:
            rec.update(verdict="BADCASE", detail=f"mutation anchor found {text.count(old)} times")
            results.append(rec)
            continue
        mutated = text.replace(old, new)
        rec.update(mutation_sha256=sha(mutated), mutation_diff_sha256=sha(old + "\n=>\n" + new))
        t0 = time.monotonic()
        try:
            path.write_text(mutated)
            r = subprocess.run(cmd, cwd=ROOT / cwd, capture_output=True, text=True, timeout=240,
                               env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", **env))
            out = r.stdout + r.stderr
            failed = re.findall(r"^FAILED (\S+)", out, flags=re.M)
            errored = bool(re.search(r"\b\d+ errors?\b", out.splitlines()[-1] if out.splitlines() else ""))
            if r.returncode == 1 and not errored and any(expect in f for f in failed):
                verdict = "CAUGHT"
            elif r.returncode == 0:
                verdict = "MISSED"
            else:
                verdict = "WRONG_REASON"
            rec.update(returncode=r.returncode, failed_tests=failed)
        except subprocess.TimeoutExpired as exc:
            out = (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            verdict = "INCONCLUSIVE_TIMEOUT"
            rec.update(returncode=None, failed_tests=[])
        finally:
            path.write_bytes(original)
            assert sha(path.read_bytes()) == rec["file_sha256"], f"{rel} not restored"
        rec.update(verdict=verdict, seconds=round(time.monotonic() - t0, 1), output_sha256=sha(out))
        (OUT / "outputs" / f"{cid}.txt").write_text(out)
        results.append(rec)
        print(f"{verdict:22} {cid:28} {rec['seconds']}s", flush=True)
    summary = {v: sum(r["verdict"] == v for r in results) for v in sorted({r["verdict"] for r in results})}
    stray = [str(p) for p in ROOT.rglob("*.pyc") if ".git" not in p.parts]
    doc = {"commit": head, "required_cases": len(CASES), "summary": summary, "results": results,
           "bytecode_written_during_run": stray,
           "not_required": NOT_REQUIRED, "pg_url_host": PG.split("@")[-1]}
    (OUT / "results.json").write_text(json.dumps(doc, indent=1, sort_keys=True))
    print(json.dumps({"commit": head, "summary": summary}), flush=True)
    sys.exit(0 if summary == {"CAUGHT": len(CASES)} else 1)


if __name__ == "__main__":
    main()
