"""Intelligence reports exercised end to end against a real PostgreSQL.

WHY A REAL DATABASE. The properties under test are storage properties — immutability of a
frozen pre-release report, version derivation under concurrency, idempotence keyed on an input
fingerprint, and the refusal to reconstruct a baseline that never existed. None of those can be
established against a generator in isolation, and the templates' accountability guarantee is
exactly the one that fails quietly if persistence is wrong.

Scenarios print a measurement. Set STOCKAI_INTEL_DB to a throwaway database.
"""
import json
import os
import pathlib
import sys
import types
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "shared"))
sys.path.insert(0, str(ROOT / "services" / "research-engine"))

for m in ["redis", "httpx", "structlog", "yfinance", "pandas"]:
    sys.modules.setdefault(m, MagicMock())

import common                                                        # noqa: E402
_cfg = types.ModuleType("common.config")
# PostgreSQL when STOCKAI_INTEL_DB names one — that is where the transaction and concurrency
# guarantees are real. Falls back to a throwaway SQLite file so the same scenarios also run in
# `make test`, where no database server exists. The fallback is NOT silent: `_meta.server` says
# which engine produced the result, because a guarantee shown only on SQLite is a weaker claim.
import tempfile                                                      # noqa: E402
_DB_URL = os.environ.get("STOCKAI_INTEL_DB") or (
    "sqlite:///" + str(pathlib.Path(tempfile.mkdtemp()) / "intel.db"))
_cfg.get_settings = lambda: types.SimpleNamespace(
    database_url=_DB_URL, admin_password=None,
    jwt_secret="x", email_provider="", email_from="")
sys.modules["common.config"] = _cfg
common.config = _cfg

from sqlalchemy import create_engine, text                           # noqa: E402
from sqlalchemy.orm import sessionmaker                              # noqa: E402
from db.models import (Base, EarningsCoverageAttempt, EarningsEvent,  # noqa: E402
                       IntelligenceReport, IssuerDocument,
                       Price, Signal, Stock, TimeFrame)
from db.models import Market, Exchange, SignalType, SignalHorizon    # noqa: E402
from src.intel_reports import adapters as A
from src.intel_reports import documents as D
from src.intel_reports import generators as G                             # noqa: E402
from src.intel_reports import store as S                                 # noqa: E402
from src.intel_reports.markdown import to_markdown                        # noqa: E402
from intelligence.report_contract import FieldState, StatementClass  # noqa: E402
import threading                                                     # noqa: E402

ENGINE = create_engine(_DB_URL)
Base.metadata.create_all(ENGINE)
Session = sessionmaker(bind=ENGINE)
R = {}
NOW = datetime(2026, 10, 2, 20, 0)


def reset(*, bars=70, with_event=True, event_in_future=True, actuals=False):
    with Session() as s:
        # DELETE rather than TRUNCATE: the same statement works on both engines, and these
        # fixtures are small enough that the speed difference is irrelevant.
        for table in ("issuer_documents", "earnings_coverage_attempts",
                      "intelligence_reports", "earnings_events", "signals", "prices", "stocks"):
            s.execute(text(f"DELETE FROM {table}"))
        stock = Stock(symbol="TESTCO", name="Test Company", market=Market.US,
                      exchange=Exchange.NASDAQ, sector="Technology", currency="USD")
        spy = Stock(symbol="SPY", name="S&P 500 ETF", market=Market.US,
                    exchange=Exchange.NASDAQ, sector="ETF", currency="USD")
        s.add_all([stock, spy]); s.flush()
        # Explicit ids: Price.id and Signal.id are BigInteger, and SQLite only auto-increments
        # a plain INTEGER primary key — so the same fixture has to supply them to run on both
        # engines rather than quietly working on PostgreSQL alone.
        pid = 0
        for st, base in ((stock, 100.0), (spy, 400.0)):
            for i in range(bars):
                pid += 1
                ts = NOW - timedelta(days=i)
                px = base + (bars - i) * 0.5
                s.add(Price(id=pid, stock_id=st.id, ts=ts, timeframe=TimeFrame.D1,
                            open=px, high=px + 1, low=px - 1, close=px, volume=1_000_000))
        s.add(Signal(id=1, stock_id=stock.id, ts=NOW - timedelta(hours=2), signal=SignalType.BUY,
                     horizon=SignalHorizon.SWING, confidence=61.0, source="signal-engine"))
        if with_event:
            d = (NOW + timedelta(days=5)).date() if event_in_future else (NOW - timedelta(days=2)).date()
            s.add(EarningsEvent(stock_id=stock.id, report_date=d, eps_estimate=1.50,
                                revenue_estimate=2_000_000.0,
                                eps_actual=1.72 if actuals else None,
                                revenue_actual=2_100_000.0 if actuals else None,
                                post_earnings_return_1d=3.4 if actuals else None))
        s.commit()


def _states(fields):
    return {k: v.state.value for k, v in fields.items()}


def t1_all_four_types_generate():
    reset()
    out = {}
    with Session() as s:
        for name, built in (
            ("market_outlook", G.market_outlook(s, market="US", now=NOW)),
            ("stock_outlook", G.stock_outlook(s, symbol="TESTCO", now=NOW)),
            ("pre_earnings", G.pre_earnings(s, symbol="TESTCO", now=NOW)),
        ):
            fields, ev, meta, cov = built
            report, created = S.save(s, fields, ev, meta, cov)
            out[name] = {"id": report.id, "status": report.status, "version": report.version,
                         "coverage": cov, "fields": len(fields),
                         "markdown_lines": len(to_markdown(report).splitlines())}
            s.expunge_all() if False else None
    reset(with_event=True, event_in_future=False, actuals=True)
    with Session() as s:
        fields, ev, meta, cov = G.post_earnings(s, symbol="TESTCO", now=NOW)
        report, _ = S.save(s, fields, ev, meta, cov)
        out["post_earnings"] = {"id": report.id, "status": report.status,
                                "coverage": cov, "fields": len(fields),
                                "markdown_lines": len(to_markdown(report).splitlines())}
    R["t1_all_four_types_generate"] = {
        **out,
        "passes": all(v["id"] and v["fields"] > 5 and v["markdown_lines"] > 10
                      for v in out.values()),
    }


def t2_identical_inputs_do_not_duplicate():
    reset()
    with Session() as s:
        a_fields, a_ev, a_meta, a_cov = G.stock_outlook(s, symbol="TESTCO", now=NOW)
        first, created_a = S.save(s, a_fields, a_ev, a_meta, a_cov)
        b_fields, b_ev, b_meta, b_cov = G.stock_outlook(s, symbol="TESTCO", now=NOW)
        second, created_b = S.save(s, b_fields, b_ev, b_meta, b_cov)
        total = s.query(IntelligenceReport).count()
        first_id, second_id = first.id, second.id
    R["t2_identical_inputs_do_not_duplicate"] = {
        "first_id": first_id, "second_id": second_id,
        "created": [created_a, created_b], "rows": total,
        "same_fingerprint": a_meta["fingerprint"] == b_meta["fingerprint"],
        "passes": first_id == second_id and created_a and not created_b and total == 1,
    }


def t3_changed_inputs_create_a_linked_version():
    reset()
    with Session() as s:
        f, e, m, c = G.stock_outlook(s, symbol="TESTCO", now=NOW)
        v1, _ = S.save(s, f, e, m, c)
        v1_payload = json.dumps(v1.payload, sort_keys=True)
        stock = s.query(Stock).filter_by(symbol="TESTCO").one()
        s.add(Price(id=999_999, stock_id=stock.id, ts=NOW + timedelta(days=1),
                    timeframe=TimeFrame.D1, open=200, high=201, low=199,
                    close=200.0, volume=5_000_000))
        s.commit()
        f2, e2, m2, c2 = G.stock_outlook(s, symbol="TESTCO", now=NOW + timedelta(days=1))
        v2, created = S.save(s, f2, e2, m2, c2)
        s.refresh(v1)
        diff = S.diff(v1, v2)
        R["t3_changed_inputs_create_a_linked_version"] = {
            "v1": v1.id, "v2": v2.id, "v2_version": v2.version,
            "supersedes": v2.supersedes_id, "v1_status": v1.status,
            "changed_fields": len(diff["changed"]),
            "v1_payload_unchanged": json.dumps(v1.payload, sort_keys=True) == v1_payload,
            "passes": (created and v2.version == 2 and v2.supersedes_id == v1.id
                       and v1.status == "superseded" and len(diff["changed"]) > 0
                       and json.dumps(v1.payload, sort_keys=True) == v1_payload),
        }


def t4_post_report_without_a_pre_report_says_so():
    """The accountability guarantee: a missing baseline is RECORDED, never reconstructed."""
    reset(with_event=True, event_in_future=False, actuals=True)
    with Session() as s:
        f, e, m, c = G.post_earnings(s, symbol="TESTCO", now=NOW)
        report, _ = S.save(s, f, e, m, c)
        link = f["pre_report_link"]
        verdict = f["thesis_verdict"]
        pre_report_id = report.pre_report_id
    R["t4_post_report_without_a_pre_report_says_so"] = {
        "pre_report_id": pre_report_id,
        "link_state": link.state.value, "link_reason": link.reason,
        "verdict_state": verdict.state.value,
        "passes": (pre_report_id is None
                   and link.state is FieldState.UNAVAILABLE
                   and "not reconstructed" in (link.reason or "").lower()
                   and verdict.state is FieldState.NOT_APPLICABLE),
    }


def t5_a_frozen_pre_report_survives_and_scores():
    reset(with_event=True, event_in_future=True)
    with Session() as s:
        f, e, m, c = G.pre_earnings(s, symbol="TESTCO", now=NOW)
        pre, _ = S.save(s, f, e, m, c)
        frozen = json.dumps(pre.payload, sort_keys=True)
        subject = m["subject_key"]
        # The release lands: actuals arrive and consensus is revised afterwards.
        ev = s.query(EarningsEvent).one()
        ev.eps_actual, ev.revenue_actual = 1.72, 2_100_000.0
        ev.post_earnings_return_1d = 3.4
        ev.eps_estimate = 1.95          # a LATER revision; must not alter the frozen report
        s.commit()
        later = NOW + timedelta(days=6)
        # The RELEASE boundary, exactly as the API now derives it — not the post-report's own
        # generation time, which is what IR-01 found being passed here.
        _, _, m_probe, _ = G.post_earnings(s, symbol="TESTCO", now=later)
        found = S.frozen_pre_report(s, subject_key=subject, before=m_probe["release_boundary"])
        f2, e2, m2, c2 = G.post_earnings(s, symbol="TESTCO", pre_report=found, now=later)
        post, _ = S.save(s, f2, e2, m2, c2)
        s.refresh(pre)
        verdict = f2["thesis_verdict"]
        pre_id, post_id, link_id = pre.id, post.id, post.pre_report_id
        unchanged = json.dumps(pre.payload, sort_keys=True) == frozen
    R["t5_a_frozen_pre_report_survives_and_scores"] = {
        "pre_id": pre_id, "post_id": post_id, "post_pre_link": link_id,
        "frozen_payload_unchanged": unchanged,
        "verdict": verdict.value if verdict.state is FieldState.OK else verdict.state.value,
        "passes": (found is not None and link_id == pre_id and unchanged
                   and verdict.state is FieldState.OK
                   and verdict.value["frozen_consensus_eps"] == 1.50),
    }


def t6_a_report_written_after_the_release_is_not_a_pre_report():
    """A pre-report generated AFTER the cutoff must not qualify as the frozen baseline."""
    reset(with_event=True, event_in_future=False, actuals=True)
    with Session() as s:
        ev = s.query(EarningsEvent).one()
        release = datetime.combine(ev.report_date, datetime.min.time())
        # Hand-write a "pre" report dated after the release — the reconstruction case.
        late = IntelligenceReport(
            report_type="pre_earnings", subject_key=f"earnings:TESTCO:{ev.report_date}",
            symbol="TESTCO", market="US", version=1, status="partial",
            contract_version=1, policy_version="1",
            generated_at=release + timedelta(days=1), cutoff_at=release + timedelta(days=1),
            input_fingerprint="late", payload={"fields": {}}, coverage={})
        s.add(late); s.commit()
        found = S.frozen_pre_report(s, subject_key=late.subject_key, before=release)
        late_id, found_id = late.id, getattr(found, "id", None)
    R["t6_a_report_written_after_the_release_is_not_a_pre_report"] = {
        "late_report_id": late_id, "found": found_id,
        "passes": found_id is None,
    }


def t7_partial_inputs_still_produce_a_useful_report():
    """Thin data must yield a partial report that NAMES what is missing, not an error."""
    reset(bars=3, with_event=False)
    with Session() as s:
        f, e, m, c = G.stock_outlook(s, symbol="TESTCO", now=NOW)
        report, _ = S.save(s, f, e, m, c)
        states = _states(f)
        md = to_markdown(report)
        status = report.status
    missing = [k for k, v in states.items() if v != "OK"]
    R["t7_partial_inputs_still_produce_a_useful_report"] = {
        "status": status, "missing_fields": len(missing),
        "every_missing_field_has_a_reason": all(
            f[k].reason for k in missing),
        "markdown_has_why_section": "Not reported, and why" in md,
        "passes": (status == "partial" and missing
                   and all(f[k].reason for k in missing)
                   and "Not reported, and why" in md),
    }


def t8_fiscal_period_is_never_asserted_from_the_release_month():
    reset(with_event=True, event_in_future=True)
    with Session() as s:
        ev = s.query(EarningsEvent).one()
        ev.fiscal_quarter, ev.fiscal_year = 3, 2026      # the WRONG, inferred label
        s.commit()
        f, _, _, _ = G.pre_earnings(s, symbol="TESTCO", now=NOW)
        fp = f["fiscal_period"]
    R["t8_fiscal_period_is_never_asserted_from_the_release_month"] = {
        "state": fp.state.value, "reason": fp.reason,
        "value_is_absent": fp.value is None,
        "passes": fp.state is FieldState.UNKNOWN and fp.value is None,
    }


def t9_zero_estimate_gives_no_percentage_surprise():
    from src.intel_reports.adapters import surprise_pct
    cases = {
        "normal": surprise_pct(1.50, 1.72, label="eps"),
        "zero_estimate": surprise_pct(0.0, 0.25, label="eps"),
        "negative_estimate": surprise_pct(-0.50, -0.20, label="eps"),
        "missing_actual": surprise_pct(1.50, None, label="eps"),
    }
    R["t9_zero_estimate_gives_no_percentage_surprise"] = {
        k: {"state": v.state.value,
            "value": v.value if v.state is FieldState.OK else v.reason}
        for k, v in cases.items()
    } | {
        "passes": (cases["normal"].state is FieldState.OK
                   and cases["zero_estimate"].state is FieldState.NOT_APPLICABLE
                   and "absolute difference" in (cases["zero_estimate"].reason or "")
                   # a negative estimate uses abs() in the denominator, so the SIGN of the
                   # surprise follows the difference and does not invert
                   and cases["negative_estimate"].value["pct"] > 0
                   and cases["missing_actual"].state is FieldState.UNAVAILABLE),
    }


def t10_market_report_labels_its_proxies():
    reset()
    with Session() as s:
        f, _, _, _ = G.market_outlook(s, market="US", now=NOW)
    breadth, bench = f["breadth"], f["benchmark"]
    R["t10_market_report_labels_its_proxies"] = {
        "breadth_state": breadth.state.value,
        "breadth_basis": (breadth.value or {}).get("basis") if breadth.state is FieldState.OK else None,
        "both_denominators": all(k in (breadth.value or {}) for k in
                                 ("participation_pct", "coverage_pct")),
        "benchmark_basis": (bench.value or {}).get("basis") if bench.state is FieldState.OK else None,
        "volatility_state": f["volatility"].state.value,
        "passes": (breadth.state is FieldState.OK
                   and "not an index constituent list" in (breadth.value or {}).get("basis", "")
                   and "proxy" in (bench.value or {}).get("basis", "")
                   and f["volatility"].state is FieldState.UNAVAILABLE),
    }


def t11_horizons_refuse_rather_than_repeat_one_heuristic():
    """IR-03. Three horizons that restate one daily rule are three times the confidence with
    none of the evidence. The daily structure is reported once; the horizons say what is
    missing."""
    reset()
    with Session() as s:
        f, _, _, _ = G.stock_outlook(s, symbol="TESTCO", now=NOW)
    horizons = {k: v for k, v in f.items() if k.startswith("outlook_")}
    struct = f["observed_daily_structure"]
    R["t11_horizons_refuse_rather_than_repeat_one_heuristic"] = {
        "horizon_states": {k: v.state.value for k, v in horizons.items()},
        "horizon_reasons_distinct": len({v.reason for v in horizons.values()}) == 3,
        "daily_structure_state": struct.state.value,
        "signal_field_name": "signal_engine_assessment" in f,
        "passes": (len(horizons) == 3
                   and all(v.state is FieldState.UNAVAILABLE for v in horizons.values())
                   and struct.state is FieldState.OK
                   and "signal_engine_assessment" in f
                   and "decision_engine_assessment" not in f),
    }


def t12_a_bearish_reading_is_invalidated_by_a_move_up():
    """IR-03. Both confirmation and invalidation were written for the constructive case, so a
    below-average reading named the very condition that supported it as its invalidation."""
    reset()
    with Session() as s:
        stock = s.query(Stock).filter_by(symbol="TESTCO").one()
        s.add(Price(id=900_001, stock_id=stock.id, ts=NOW + timedelta(days=1),
                    timeframe=TimeFrame.D1, open=10, high=11, low=9, close=10.0, volume=1e6))
        s.commit()
        f, _, _, _ = G.stock_outlook(s, symbol="TESTCO", now=NOW + timedelta(days=1))
    v = f["observed_daily_structure"].value
    R["t12_a_bearish_reading_is_invalidated_by_a_move_up"] = {
        "reading": v["reading"], "what_would_change_it": v["what_would_change_this_reading"],
        "passes": ("below" in v["reading"]
                   and v["what_would_change_this_reading"].startswith("a daily close above")),
    }


def t13_a_historical_cutoff_cannot_consume_later_prices():
    """IR-02. The generator read the latest bars regardless of the requested cutoff, so a
    25 September report quoted the 2 October close."""
    reset()
    cutoff = NOW - timedelta(days=7)
    with Session() as s:
        f, book, _, _ = G.stock_outlook(s, symbol="TESTCO", now=cutoff)
    px = f["price_as_of"]
    R["t13_a_historical_cutoff_cannot_consume_later_prices"] = {
        "cutoff": cutoff.isoformat(),
        "price_ts_used": (px.value or {}).get("ts"),
        "evidence_records": len(book.records),
        "passes": ((px.value or {}).get("ts", "") <= cutoff.isoformat()
                   and len(book.records) > 0),
    }


def t14_every_citation_resolves():
    """IR-02. Fields carried ids like price:3:2026-10-02 that matched no stored record — a
    citation that looks checkable and is not."""
    reset()
    results = {}
    # LOAD-BEARING FIELDS MUST ACTUALLY CITE. Checking only that no reference DANGLES is too
    # weak: a field that cites nothing at all trivially passes it. Removing the price evidence
    # entirely was caught by nothing until these were named.
    must_cite = {
        "market": ["price_as_of", "trend_structure", "return_5_bars"],
        "stock": ["price_as_of", "trend_structure", "return_5_bars",
                  "signal_engine_assessment"],
        "pre": ["pre_event_reference_price", "trend_structure", "consensus_eps"],
    }
    with Session() as s:
        for name, built in (("market", G.market_outlook(s, market="US", now=NOW)),
                            ("stock", G.stock_outlook(s, symbol="TESTCO", now=NOW)),
                            ("pre", G.pre_earnings(s, symbol="TESTCO", now=NOW))):
            fields, book, _, _ = built
            cited = {e for f in fields.values() for e in (f.evidence_ids or [])}
            uncited = [k for k in must_cite[name]
                       if k in fields and fields[k].state is FieldState.OK
                       and not (fields[k].evidence_ids or [])]
            results[name] = {"records": len(book.records), "cited": len(cited),
                             "dangling": book.dangling(fields),
                             "load_bearing_without_citation": uncited}
    R["t14_every_citation_resolves"] = {
        **results,
        "passes": all(not v["dangling"] and v["records"] > 0
                      and not v["load_bearing_without_citation"] for v in results.values()),
    }


def t15_a_stale_price_degrades_what_is_derived_from_it():
    """IR-02. trend_structure read the same bars price_as_of had just flagged STALE, and
    presented them as current evidence."""
    reset()
    far_future = NOW + timedelta(days=40)
    with Session() as s:
        f, _, _, _ = G.stock_outlook(s, symbol="TESTCO", now=far_future)
    R["t15_a_stale_price_degrades_what_is_derived_from_it"] = {
        "price_state": f["price_as_of"].state.value,
        "trend_state": f["trend_structure"].state.value,
        "horizon_states": {k: v.state.value for k, v in f.items() if k.startswith("outlook_")},
        "passes": (f["price_as_of"].state is FieldState.STALE
                   and f["trend_structure"].state is FieldState.STALE),
    }


def t16_a_pre_report_cannot_be_written_once_the_release_is_known():
    """IR-01. report_date >= today admitted an event that reported earlier the SAME DAY."""
    reset(with_event=True, event_in_future=False, actuals=True)
    outcomes = {}
    with Session() as s:
        try:
            G.pre_earnings(s, symbol="TESTCO", now=NOW)
            outcomes["after_release"] = "ALLOWED"
        except LookupError as exc:
            outcomes["after_release"] = str(exc)[:90]
    # ...and on the release DAY itself, where only a date is stored and the time is unknown.
    reset(with_event=True, event_in_future=True)
    with Session() as s:
        ev = s.query(EarningsEvent).one()
        same_day = datetime.combine(ev.report_date, datetime.min.time()) + timedelta(hours=12)
        try:
            G.pre_earnings(s, symbol="TESTCO", now=same_day)
            outcomes["on_release_day"] = "ALLOWED"
        except LookupError as exc:
            outcomes["on_release_day"] = str(exc)[:90]
    R["t16_a_pre_report_cannot_be_written_once_the_release_is_known"] = {
        **outcomes,
        "passes": all(v != "ALLOWED" for v in outcomes.values()),
    }


def t17_the_surprise_table_uses_the_frozen_expectation():
    """IR-04. The table read the mutable current estimate while the verdict read the frozen one,
    so the same report showed a -11.79% miss beside a verdict of 'above'."""
    reset(with_event=True, event_in_future=True)
    with Session() as s:
        f, book, m, c = G.pre_earnings(s, symbol="TESTCO", now=NOW)
        pre, _ = S.save(s, f, book, m, c)
        ev = s.query(EarningsEvent).one()
        ev.eps_actual, ev.post_earnings_return_1d = 1.72, 3.4
        ev.eps_estimate = 1.95                      # revised AFTER the freeze
        s.commit()
        f2, _, _, _ = G.post_earnings(
            s, symbol="TESTCO", pre_report=pre, now=NOW + timedelta(days=6))
    R["t17_the_surprise_table_uses_the_frozen_expectation"] = {
        "surprise_pct": f2["eps_surprise_pct"].value,
        "expectation": f2["eps_expectation"].value,
        "revision_field_present": "eps_estimate_revised_since" in f2,
        "verdict": f2["thesis_verdict"].value,
        "stage": f2["stage"].value["stage"],
        "passes": (f2["eps_expectation"].value["is_frozen"] is True
                   and f2["eps_expectation"].value["value"] == 1.50
                   and f2["eps_surprise_pct"].value["pct"] > 0
                   and "eps_estimate_revised_since" in f2
                   and f2["thesis_verdict"].value["thesis_evaluation"] == "not_evaluable"
                   and f2["stage"].value["stage"] == "FIRST_FLASH"),
    }


def t18_concurrent_generation_allocates_one_version_each():
    """IR-05. save() reads the max version and adds one; two requests can read the same answer.
    Only a database constraint prevents the duplicate — this races real connections at it.

    Meaningful on PostgreSQL; on SQLite the writers serialise, so the result is recorded with
    the engine name rather than presented as a concurrency proof either way.
    """
    reset()
    barrier = threading.Barrier(4)
    results, errors = [], []
    lock = threading.Lock()

    def worker(tag):
        try:
            with Session() as s:
                # Different inputs per worker, so every one of them must create a NEW version
                # rather than dedupe onto the same fingerprint.
                f, book, m, c = G.stock_outlook(s, symbol="TESTCO", now=NOW)
                m = dict(m); m["fingerprint"] = f"race-{tag}"
                barrier.wait(10)
                r, created = S.save(s, f, book, m, c)
                with lock:
                    results.append((r.version, created))
        except Exception as exc:                       # noqa: BLE001
            with lock:
                errors.append(f"{type(exc).__name__}: {exc}"[:120])

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    [t.start() for t in threads]; [t.join(30) for t in threads]
    versions = sorted(v for v, _ in results)
    R["t18_concurrent_generation_allocates_one_version_each"] = {
        "engine": ENGINE.dialect.name,
        "versions": versions,
        "errors": errors,
        "unique_versions": len(set(versions)) == len(versions),
        "passes": len(set(versions)) == len(versions) and not errors,
    }


# ─────────────────────────────────────────────────────────────────────────────
# t19-t23 — the screenshot round. Every witness below is MU's real stored data.
# ─────────────────────────────────────────────────────────────────────────────

#: MU's actual stored values, read from production on 2026-10-03. The producer computes
#: `price / baseline - 1`, so these are FRACTIONS while its own docstring calls them "% change".
_MU_RETURNS = {
    "2026-06-24": (0.15382642030697258, -0.01853060584867272),
    "2026-03-18": (-0.03773093864746768, -0.17241002781816028),
    "2025-12-17": (0.06898626640088268, 0.23297929400373185),
}


def t19_fractional_returns_are_converted_once():
    """A stored 0.15382 means +15.38%, and was rendered "0.1538 pct" — a hundredfold error in
    the most quotable number the report carries."""
    reset(with_event=True, event_in_future=False, actuals=True)
    with Session() as s:
        ev = s.query(EarningsEvent).one()
        ev.post_earnings_return_1d = _MU_RETURNS["2026-06-24"][0]
        ev.post_earnings_return_5d = _MU_RETURNS["2026-06-24"][1]
        s.commit()
        f, _, _, _ = G.post_earnings(s, symbol="TESTCO", now=NOW)
    r1, r5 = f["return_1d"], f["return_5d"]
    R["t19_fractional_returns_are_converted_once"] = {
        "stored_1d": _MU_RETURNS["2026-06-24"][0],
        "reported_1d": r1.value, "units_1d": r1.units,
        "reported_5d": r5.value,
        "passes": (abs(r1.value["pct"] - 15.38) < 0.01 and r1.units == "pct"
                   and abs(r5.value["pct"] + 1.85) < 0.01
                   and "not normalised" in r1.value["note"]
                   # The window is named by its ACTUAL dates, not by a span word.
                   and " close to " in r1.value["window"]
                   and "1 session" not in r1.value["window"]),
    }


def t25_the_reaction_window_names_its_real_interval():
    """The producer's `return_1d` runs from the last close BEFORE the report date to the day
    AFTER it — two close-to-close intervals for a trading-day release. The review's own
    execution: 29 Sept close 100, 30 Sept close 110, 1 Oct close 121 -> 0.21, which is 21%
    across two intervals, not either one-session move of 10%."""
    reset(with_event=False)
    with Session() as s:
        stock = s.query(Stock).filter_by(symbol="TESTCO").one()
        s.execute(text("DELETE FROM prices"))
        closes = {date(2026, 9, 29): 100.0, date(2026, 9, 30): 110.0, date(2026, 10, 1): 121.0,
                  date(2026, 10, 2): 121.0, date(2026, 10, 5): 121.0}
        for i, (d, c) in enumerate(closes.items()):
            s.add(Price(id=700_000 + i, stock_id=stock.id,
                        ts=datetime.combine(d, datetime.min.time()),
                        timeframe=TimeFrame.D1, open=c, high=c, low=c, close=c, volume=1e6))
        s.add(EarningsEvent(stock_id=stock.id, report_date=date(2026, 9, 30),
                            eps_estimate=1.0, eps_actual=1.2,
                            post_earnings_return_1d=0.21))
        s.commit()
        wd = A.reaction_window_dates(s, stock.id, date(2026, 9, 30))
        f, _, _, _ = G.post_earnings(s, symbol="TESTCO", now=datetime(2026, 10, 6))
    r1 = f["return_1d"]
    R["t25_the_reaction_window_names_its_real_interval"] = {
        "window_dates": wd,
        "reported": r1.value,
        "passes": (wd["baseline_date"] == "2026-09-29"
                   and wd["endpoint_1d"] == "2026-10-01"
                   and wd["intervals_1d"] == 2
                   and abs(r1.value["pct"] - 21.0) < 0.01
                   and "2026-09-29 close to 2026-10-01 close" == r1.value["window"]
                   and "2 close-to-close interval" in r1.value["basis"]),
    }


def t20_historical_reaction_summaries_use_the_same_unit():
    reset(with_event=True, event_in_future=True)
    with Session() as s:
        stock = s.query(Stock).filter_by(symbol="TESTCO").one()
        for i, (d, (r1, _)) in enumerate(_MU_RETURNS.items()):
            s.add(EarningsEvent(stock_id=stock.id, report_date=date.fromisoformat(d),
                                eps_estimate=1.0, eps_actual=1.1,
                                post_earnings_return_1d=r1))
        s.commit()
        f, _, _, _ = G.pre_earnings(s, symbol="TESTCO", now=NOW)
    hr = f["historical_reactions"]
    R["t20_historical_reaction_summaries_use_the_same_unit"] = {
        "state": hr.state.value,
        "value": hr.value if hr.state is FieldState.OK else hr.reason,
        # median of {15.38, -3.77, 6.90} is 6.90, not 0.069
        "passes": (hr.state is FieldState.OK
                   and abs(hr.value["median_1d_pct"] - 6.90) < 0.01
                   and abs(hr.value["max_pct"] - 15.38) < 0.01),
    }


def t21_a_reused_report_is_not_called_a_first_report():
    """The card said v2 supersedes #1 while its comparison said "first report"."""
    reset()
    with Session() as s:
        f, b, m, c = G.stock_outlook(s, symbol="TESTCO", now=NOW)
        v1, _ = S.save(s, f, b, m, c)
        stock = s.query(Stock).filter_by(symbol="TESTCO").one()
        s.add(Price(id=990_001, stock_id=stock.id, ts=NOW + timedelta(days=1),
                    timeframe=TimeFrame.D1, open=200, high=201, low=199, close=200.0,
                    volume=1e6))
        s.commit()
        later = NOW + timedelta(days=1)
        f2, b2, m2, c2 = G.stock_outlook(s, symbol="TESTCO", now=later)
        v2, created2 = S.save(s, f2, b2, m2, c2)
        # Regenerate with the SAME inputs: save() returns the existing v2.
        f3, b3, m3, c3 = G.stock_outlook(s, symbol="TESTCO", now=later)
        v2_again, created3 = S.save(s, f3, b3, m3, c3)
        predecessor = s.get(IntelligenceReport, v2_again.supersedes_id) \
            if v2_again.supersedes_id else None
        d_reused = S.diff(predecessor, v2_again)
        d_no_baseline = S.diff(None, v2_again)
        ids = (v1.id, v2.id, v2_again.id, v2_again.supersedes_id)
    R["t21_a_reused_report_is_not_called_a_first_report"] = {
        "ids": ids, "created": [created2, created3],
        "reused_diff_first_report": d_reused["first_report"],
        "changed_fields": len(d_reused["changed"]),
        "no_baseline_still_not_first": d_no_baseline["first_report"],
        "passes": (ids[1] == ids[2] and created2 and not created3
                   and d_reused["first_report"] is False
                   and d_no_baseline["first_report"] is False),
    }


def t22_a_bar_date_is_not_an_availability_time():
    """A daily bar timestamped at midnight does not establish that its CLOSE was knowable
    then — and a backfilled row cannot acquire availability from the date it describes."""
    reset()
    with Session() as s:
        _, book, _, _ = G.stock_outlook(s, symbol="TESTCO", now=NOW)
    price_records = [r for k, r in book.records.items() if k.startswith("price:")]
    sample = price_records[0] if price_records else {}
    R["t22_a_bar_date_is_not_an_availability_time"] = {
        "price_records": len(price_records),
        "first_available_at": sample.get("first_available_at"),
        "published_at": sample.get("published_at"),
        "state": sample.get("state"),
        "has_limitation_note": "not established" in (sample.get("note") or ""),
        "passes": (len(price_records) > 0
                   and sample.get("first_available_at") is None
                   and sample.get("published_at") is None
                   and "not established" in (sample.get("note") or "")),
    }


def t23_the_release_boundary_uses_the_exchange_timezone():
    """Naive midnight read as UTC is not conservative both ways: HK is UTC+8, so 20:00 UTC the
    previous calendar day is already 04:00 on the HK release day."""
    class _Ev:
        report_date = date(2026, 10, 2)
    us = G.release_boundary(_Ev(), "US")
    hk = G.release_boundary(_Ev(), "HK")
    naive = datetime(2026, 10, 2, 0, 0)
    R["t23_the_release_boundary_uses_the_exchange_timezone"] = {
        "naive_midnight": naive.isoformat(),
        "us_boundary_utc": us.isoformat(),
        "hk_boundary_utc": hk.isoformat(),
        # HK must move EARLIER than naive midnight, US later.
        "passes": hk < naive < us,
    }


def t24_a_stale_newest_event_is_flagged_not_substituted():
    """MU on 3 October analysed the 24 June event, correctly by its own rule, because the
    30 September release is not ingested. The rule was right; silence about it was not."""
    reset(with_event=False)
    # MU's REAL release dates, which is the case this exists for: on 3 October the newest
    # released event on file was 24 June — 101 days old — because 30 September was never
    # ingested. A fixed 115-day threshold did not fire for it; the issuer's own median gap of
    # 95 days does.
    mu_dates = ["2025-05-31", "2025-09-23", "2025-12-17", "2026-03-18", "2026-06-24"]
    with Session() as s:
        stock = s.query(Stock).filter_by(symbol="TESTCO").one()
        for d in mu_dates:
            s.add(EarningsEvent(stock_id=stock.id, report_date=date.fromisoformat(d),
                                eps_estimate=1.0, eps_actual=1.2, post_earnings_return_1d=0.05))
        s.add(EarningsEvent(stock_id=stock.id, report_date=date(2026, 12, 23), eps_estimate=1.3))
        s.commit()
        just_after = datetime(2026, 6, 30)          # 6 days after the newest release
        fresh, _, _, _ = G.post_earnings(s, symbol="TESTCO", now=just_after)
        mu_day, _, _, _ = G.post_earnings(s, symbol="TESTCO", now=datetime(2026, 10, 3))
    R["t24_a_stale_newest_event_is_flagged_not_substituted"] = {
        "fresh_state": fresh["event_coverage"].state.value,
        "fresh_age": fresh["event_coverage"].value["age_days"],
        "mu_state": mu_day["event_coverage"].state.value,
        "mu_detail": mu_day["event_coverage"].value,
        "mu_reason": mu_day["event_coverage"].reason,
        "fresh_statement": fresh["event_coverage"].statement.value,
        "mu_statement": mu_day["event_coverage"].statement.value,
        "fresh_coverage_state": fresh["event_coverage"].value["coverage_state"],
        "mu_coverage_state": mu_day["event_coverage"].value["coverage_state"],
        "passes": (
            # Within the median, the absence of a warning certifies nothing.
            fresh["event_coverage"].state is FieldState.UNKNOWN
            and fresh["event_coverage"].value["coverage_state"] == "coverage_unknown"
            and "rules nothing out" in (fresh["event_coverage"].reason or "")
            # Past it, a SUSPICION — classified as an inference, never as an observed fact.
            and mu_day["event_coverage"].state is FieldState.CONFLICTING
            and mu_day["event_coverage"].value["coverage_state"] == "suspected_gap"
            and mu_day["event_coverage"].statement.value == "interpretation"
            and mu_day["event_coverage"].value["age_days"] == 101
            and "POSSIBLE COVERAGE GAP" in (mu_day["event_coverage"].reason or "")
            and "not proof" in (mu_day["event_coverage"].reason or "")),
    }


# ─────────────────────────────────────────────────────────────────────────────
# t26-t28 — the official release joined to the report.
# Figures are Micron's REAL fiscal Q4 2026 release (period end 2026-09-03).
# ─────────────────────────────────────────────────────────────────────────────

_MU_RELEASE_FACTS = {
    "revenue": {"value": 54.23e9, "units": "USD", "basis": "GAAP", "period": "fiscal Q4 2026"},
    "eps_adjusted": {"value": 33.42, "units": "USD/share", "basis": "non-GAAP adjusted",
                     "period": "fiscal Q4 2026"},
    # BOTH margins, because they are different numbers and collapsing them loses the
    # distinction the issuer itself drew.
    "gross_margin_gaap": {"value": 86.8, "units": "pct", "basis": "GAAP"},
    "gross_margin_non_gaap": {"value": 87.0, "units": "pct", "basis": "non-GAAP"},
    "operating_cash_flow": {"value": 43.97e9, "units": "USD", "basis": "GAAP"},
    "adjusted_free_cash_flow": {"value": 33.20e9, "units": "USD", "basis": "non-GAAP"},
    "guidance_next_q_revenue": {"value": 61.5e9, "range": 1.5e9, "units": "USD",
                                "basis": "company guidance", "period": "fiscal Q1 2027"},
    "guidance_next_q_eps_adjusted": {"value": 38.15, "range": 1.00, "units": "USD/share",
                                     "basis": "non-GAAP company guidance",
                                     "period": "fiscal Q1 2027"},
    # NOT described as cash: the $73.48B balance includes marketable investments and
    # restricted cash, and calling it "cash" overstates what is actually available.
    "cash_and_investments": {"value": 73.48e9, "units": "USD", "basis": "GAAP",
                             "note": "includes marketable investments and restricted cash; "
                                     "not all unrestricted cash"},
}


def _add_release(s, stock_id, *, period_end, published, url, facts=None, event_id=None):
    doc = IssuerDocument(
        stock_id=stock_id, document_type="press_release",
        source_url=url, publisher="Micron Technology, Inc.",
        title="Micron Technology, Inc. Reports Results",
        fiscal_period_end=period_end, fiscal_label="FY2026 Q4",
        fiscal_source="stated in the release headline",
        published_at=published, retrieved_at=published + timedelta(hours=1),
        content_hash="sha256:" + "0" * 16, facts=facts or {}, event_id=event_id)
    s.add(doc); s.flush()
    return doc


def t26_the_official_release_is_joined_by_period():
    reset(with_event=True, event_in_future=False, actuals=True)
    with Session() as s:
        stock = s.query(Stock).filter_by(symbol="TESTCO").one()
        ev = s.query(EarningsEvent).one()
        _add_release(s, stock.id, period_end=ev.report_date - timedelta(days=20),
                     published=datetime.combine(ev.report_date, datetime.min.time()),
                     url="https://investors.example.invalid/q4-2026",
                     facts=_MU_RELEASE_FACTS)
        s.commit()
        f, book, _, _ = G.post_earnings(s, symbol="TESTCO", now=NOW)
    rel, fiscal, figs = (f["official_release"], f["source_confirmed_fiscal_period"],
                         f["official_figures"])
    R["t26_the_official_release_is_joined_by_period"] = {
        "release_state": rel.state.value,
        "matched_on": (rel.value or {}).get("matched_on"),
        "fiscal_state": fiscal.state.value,
        "fiscal": fiscal.value,
        "figures_state": figs.state.value,
        "gaap_vs_non_gaap_both_present": all(
            k in (figs.value or {}) for k in ("gross_margin_gaap", "gross_margin_non_gaap")),
        "cited": bool(rel.evidence_ids) and not book.dangling(f),
        "passes": (rel.state is FieldState.OK
                   and "source-confirmed" in (rel.value or {}).get("matched_on", "")
                   and fiscal.state is FieldState.OK
                   and figs.state is FieldState.OK
                   and (figs.value or {})["gross_margin_gaap"]["value"] == 86.8
                   and (figs.value or {})["gross_margin_non_gaap"]["value"] == 87.0
                   and not book.dangling(f)),
    }


def t27_a_release_without_an_event_confirms_the_gap():
    """THE PROMOTION CADENCE CANNOT MAKE. A dated official release naming a period the event
    table does not contain is evidence, not an inference about reporting rhythm."""
    reset(with_event=False)
    with Session() as s:
        stock = s.query(Stock).filter_by(symbol="TESTCO").one()
        # An old event exists (so a post-report can be built at all) ...
        s.add(EarningsEvent(stock_id=stock.id, report_date=date(2026, 6, 24),
                            eps_estimate=1.0, eps_actual=1.2, post_earnings_return_1d=0.05))
        s.flush()
        # ... and a release for a LATER period that has no event row — MU's real shape.
        _add_release(s, stock.id, period_end=date(2026, 9, 3),
                     published=datetime(2026, 9, 30, 20, 5),
                     url="https://investors.example.invalid/fq4-2026",
                     facts=_MU_RELEASE_FACTS)
        s.commit()
        f, book, _, _ = G.post_earnings(s, symbol="TESTCO", now=datetime(2026, 10, 3))
    ec = f["event_coverage"]
    R["t27_a_release_without_an_event_confirms_the_gap"] = {
        "coverage_state": (ec.value or {}).get("coverage_state"),
        "statement": ec.statement.value,
        "orphans": (ec.value or {}).get("orphan_documents"),
        "reason": (ec.reason or "")[:120],
        "cited": bool(ec.evidence_ids),
        "passes": ((ec.value or {}).get("coverage_state") == "confirmed_missing_event"
                   and ec.statement is StatementClass.OBSERVED_FACT
                   and len((ec.value or {}).get("orphan_documents") or []) == 1
                   and (ec.value or {})["orphan_documents"][0]["fiscal_period_end"] == "2026-09-03"
                   and bool(ec.evidence_ids)),
    }


def t28_a_document_join_never_depends_on_an_event_row():
    """Resolving by issuer and period, not by event id, is what makes t27 expressible at all."""
    reset(with_event=False)
    with Session() as s:
        stock = s.query(Stock).filter_by(symbol="TESTCO").one()
        doc = _add_release(s, stock.id, period_end=date(2026, 9, 3),
                           published=datetime(2026, 9, 30, 20, 5),
                           url="https://investors.example.invalid/no-event",
                           facts=_MU_RELEASE_FACTS)
        s.commit()
        found = D.documents_for_period(s, stock.id, period_end=date(2026, 9, 3),
                                       report_date=None)
        by_report_date_only = D.documents_for_period(s, stock.id, period_end=None,
                                                     report_date=date(2026, 9, 30))
        event_rows = s.query(EarningsEvent).count()
        doc_id, doc_event = doc.id, doc.event_id
    R["t28_a_document_join_never_depends_on_an_event_row"] = {
        "event_rows": event_rows,
        "document_event_id": doc_event,
        "found_by_period": [d.id for d in found],
        "found_by_report_date": [d.id for d in by_report_date_only],
        "passes": (event_rows == 0 and doc_event is None
                   and [d.id for d in found] == [doc_id]
                   and [d.id for d in by_report_date_only] == [doc_id]),
    }


def main():
    for fn in (t1_all_four_types_generate, t2_identical_inputs_do_not_duplicate,
               t3_changed_inputs_create_a_linked_version,
               t4_post_report_without_a_pre_report_says_so,
               t5_a_frozen_pre_report_survives_and_scores,
               t6_a_report_written_after_the_release_is_not_a_pre_report,
               t7_partial_inputs_still_produce_a_useful_report,
               t8_fiscal_period_is_never_asserted_from_the_release_month,
               t9_zero_estimate_gives_no_percentage_surprise,
               t10_market_report_labels_its_proxies,
               t11_horizons_refuse_rather_than_repeat_one_heuristic,
               t12_a_bearish_reading_is_invalidated_by_a_move_up,
               t13_a_historical_cutoff_cannot_consume_later_prices,
               t14_every_citation_resolves,
               t15_a_stale_price_degrades_what_is_derived_from_it,
               t16_a_pre_report_cannot_be_written_once_the_release_is_known,
               t17_the_surprise_table_uses_the_frozen_expectation,
               t18_concurrent_generation_allocates_one_version_each,
               t19_fractional_returns_are_converted_once,
               t20_historical_reaction_summaries_use_the_same_unit,
               t21_a_reused_report_is_not_called_a_first_report,
               t22_a_bar_date_is_not_an_availability_time,
               t23_the_release_boundary_uses_the_exchange_timezone,
               t24_a_stale_newest_event_is_flagged_not_substituted,
               t25_the_reaction_window_names_its_real_interval,
               t26_the_official_release_is_joined_by_period,
               t27_a_release_without_an_event_confirms_the_gap,
               t28_a_document_join_never_depends_on_an_event_row):
        fn()
    R["_meta"] = {"engine": ENGINE.dialect.name,
                  "server": str(ENGINE.url).split("@")[-1],
                  "postgresql": ENGINE.dialect.name == "postgresql",
                  "scope": "throwaway local database; no production access"}
    print(json.dumps(R, indent=2, default=str))


if __name__ == "__main__":
    main()
