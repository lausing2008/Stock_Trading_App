"""Read side of the Intraday research records (design §11). Pure query functions over a
connection; the router in api/intraday.py only adds auth and HTTP errors.

Rules: mode is required and never defaulted; replay needs its run_key and observed rejects one;
evaluations and setups need an explicit identity (two configs are never pooled); ordering keys are
unique and non-null per endpoint; a sealed session's membership is frozen, an unsealed one is a
labelled changing view; unresolved exposure is listed across sessions until reconciled.
"""
from __future__ import annotations

import base64
import json
from datetime import date, datetime

from sqlalchemy import Date, DateTime, and_, func, select, tuple_

from common.horizons import INTRADAY_PRODUCTION_ENABLED, require_intraday_market

from . import contract as c
from .models import evaluation_counts, evaluations, identities, seals, setups, transitions

MAX_LIMIT = 500


class BadRequest(ValueError):
    pass


def run_scope(mode: str | None, run_key: str | None) -> str:
    if mode == "observed":
        if run_key:
            raise BadRequest("mode=observed takes no run_key")
        return c.OBSERVED_RUN
    if mode == "replay":
        if not run_key or run_key == c.OBSERVED_RUN:
            raise BadRequest("mode=replay requires its run_key")
        return run_key
    raise BadRequest("mode must be 'observed' or 'replay'")


def _cursor_out(values) -> str:
    return base64.urlsafe_b64encode(json.dumps([c._norm(v) for v in values]).encode()).decode()


def _cursor_in(cursor: str | None, key_cols):
    """Decode a cursor against the endpoint's own key columns, by their types."""
    if not cursor:
        return None
    try:
        vals = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        if not isinstance(vals, list) or len(vals) != len(key_cols):
            raise ValueError("shape")
        out = []
        for col, v in zip(key_cols, vals):
            if isinstance(col.type, DateTime):
                v = datetime.fromisoformat(v.replace("Z", "+00:00")).replace(tzinfo=None)
            elif isinstance(col.type, Date):
                v = date.fromisoformat(v)
            elif not isinstance(v, str):
                raise ValueError("type")
            out.append(v)
        return out
    except Exception as exc:  # noqa: BLE001
        raise BadRequest("invalid cursor") from exc


def _limit(limit):
    if limit is None:
        return 100
    if not 1 <= int(limit) <= MAX_LIMIT:
        raise BadRequest(f"limit must be 1..{MAX_LIMIT}")
    return int(limit)


def _envelope(**kw):
    return {"production_generation": "enabled" if INTRADAY_PRODUCTION_ENABLED else "disabled",
            "contract_version": c.CONTRACT_VERSION, **kw}


def _identity_row(conn, identity):
    row = conn.execute(select(identities).where(identities.c.identity_hash == identity)).first()
    if row is None:
        raise BadRequest("unknown identity")
    return dict(identity_hash=row.identity_hash, strategy_id=row.strategy_id, strategy_version=row.strategy_version,
                strategy_source_hash=row.strategy_source_hash, contract_version=row.contract_version,
                config_hash=row.config_hash)


def _membership(conn, run_key, identity, session_date):
    s = conn.execute(select(seals.c.sealed_at).where(seals.c.run_key == run_key, seals.c.identity_hash == identity,
                                                     seals.c.session_date == session_date)).scalar()
    return ({"membership": "sealed", "sealed_at": c._norm(s)} if s is not None else
            {"membership": "changing_view",
             "note": "unsealed session: rows inserted concurrently behind the cursor can be missed; "
                     "re-read after the seal or re-scan from the start for complete coverage"})


def coverage(conn, run_key, identity, session_date):
    live = dict(conn.execute(select(evaluations.c.result, func.count()).where(
        evaluations.c.run_key == run_key, evaluations.c.identity_hash == identity,
        evaluations.c.session_date == session_date).group_by(evaluations.c.result)).all())
    archived = dict(conn.execute(select(evaluation_counts.c.result, evaluation_counts.c.count).where(
        evaluation_counts.c.run_key == run_key, evaluation_counts.c.identity_hash == identity,
        evaluation_counts.c.session_date == session_date)).all())
    return {"by_result": archived or live, "source": "archived_counts" if archived else "live_rows",
            "non_setup_rows_archived": bool(archived)}


def list_identities(conn, *, market, mode, run_key, session_date: date):
    require_intraday_market(market)
    rk = run_scope(mode, run_key)
    # Live rows AND archived counts: after retention an identity's non-setup rows exist only as
    # counts, and it must not disappear from discovery (review follow-up).
    found = {h: dict(_identity_row(conn, h), evaluations=n) for h, n in conn.execute(
        select(evaluations.c.identity_hash, func.count()).where(
            evaluations.c.run_key == rk, evaluations.c.session_date == session_date)
        .group_by(evaluations.c.identity_hash)).all()}
    for h, n in conn.execute(select(evaluation_counts.c.identity_hash, func.sum(evaluation_counts.c.count)).where(
            evaluation_counts.c.run_key == rk, evaluation_counts.c.session_date == session_date)
            .group_by(evaluation_counts.c.identity_hash)).all():
        found[h] = dict(_identity_row(conn, h), evaluations=int(n))       # archive is the full count
    return _envelope(run_key=rk, record_mode=mode, session=session_date.isoformat(),
                     identities=[found[h] for h in sorted(found)])


def _page(conn, *, table, key_cols, where, cursor, limit, row_fn):
    n = _limit(limit)
    after = _cursor_in(cursor, key_cols)
    q = select(table).where(*where)
    if after is not None:
        q = q.where(tuple_(*key_cols) > tuple_(*after))
    rows = conn.execute(q.order_by(*key_cols).limit(n + 1)).all()
    more = len(rows) > n
    rows = rows[:n]
    nxt = _cursor_out([getattr(rows[-1], k.name) for k in key_cols]) if more else None
    return [row_fn(r) for r in rows], nxt


def list_evaluations(conn, *, market, mode, run_key, identity, session_date: date, symbol=None, cursor=None, limit=None):
    require_intraday_market(market)
    rk = run_scope(mode, run_key)
    if not identity:
        raise BadRequest("identity is required; see /identities")
    ident = _identity_row(conn, identity)
    where = [evaluations.c.run_key == rk, evaluations.c.identity_hash == identity,
             evaluations.c.session_date == session_date]
    if symbol:
        where.append(evaluations.c.symbol == symbol.upper())
    keys = [evaluations.c.trigger_bar_open, evaluations.c.symbol, evaluations.c.evaluation_key]
    items, nxt = _page(conn, table=evaluations, key_cols=keys, where=where, cursor=cursor, limit=limit,
                       row_fn=lambda r: dict(evaluation_key=r.evaluation_key, identity_hash=r.identity_hash,
                                             symbol=r.symbol, trigger_bar_open=c._norm(r.trigger_bar_open),
                                             result=r.result, detected_at=c._norm(r.detected_at),
                                             data_cutoff=c._norm(r.data_cutoff)))
    return _envelope(run_key=rk, record_mode=mode, identity=ident, session=session_date.isoformat(), items=items,
                     next_cursor=nxt, coverage=coverage(conn, rk, identity, session_date),
                     **_membership(conn, rk, identity, session_date))


def _latest_state_subq():
    mx = select(transitions.c.setup_id, func.max(transitions.c.seq).label("seq")).group_by(
        transitions.c.setup_id).subquery()
    return select(transitions.c.setup_id, transitions.c.to_state).join(
        mx, and_(mx.c.setup_id == transitions.c.setup_id, mx.c.seq == transitions.c.seq)).subquery()


def _setup_row(conn, r, latest):
    return dict(occurrence_key=r.occurrence_key, identity_hash=r.identity_hash, symbol=r.symbol,
                session=r.session_date.isoformat(), trigger_bar_open=c._norm(r.trigger_bar_open),
                direction=r.direction, entry_reference=r.entry_reference, stop=r.stop, target=r.target,
                detected_at=c._norm(r.detected_at), actionable_at=c._norm(r.actionable_at),
                entry_expires_at=c._norm(r.entry_expires_at), state=latest.get(r.id),
                execution_grade=False)


def list_setups(conn, *, market, mode, run_key, identity, session_date: date, symbol=None, cursor=None, limit=None):
    require_intraday_market(market)
    rk = run_scope(mode, run_key)
    if not identity:
        raise BadRequest("identity is required; see /identities")
    ident = _identity_row(conn, identity)
    where = [setups.c.run_key == rk, setups.c.identity_hash == identity, setups.c.session_date == session_date]
    if symbol:
        where.append(setups.c.symbol == symbol.upper())
    keys = [setups.c.trigger_bar_open, setups.c.symbol, setups.c.occurrence_key]
    latest_q = _latest_state_subq()
    latest = dict(conn.execute(select(latest_q.c.setup_id, latest_q.c.to_state)).all())
    items, nxt = _page(conn, table=setups, key_cols=keys, where=where, cursor=cursor, limit=limit,
                       row_fn=lambda r: _setup_row(conn, r, latest))
    return _envelope(run_key=rk, record_mode=mode, identity=ident, session=session_date.isoformat(), items=items,
                     next_cursor=nxt, coverage=coverage(conn, rk, identity, session_date),
                     **_membership(conn, rk, identity, session_date))


def list_unresolved(conn, *, market, mode, run_key, cursor=None, limit=None):
    """Every entered-but-not-exited exposure, any session, until reconciled."""
    require_intraday_market(market)
    rk = run_scope(mode, run_key)
    latest_q = _latest_state_subq()
    ids = select(latest_q.c.setup_id).where(latest_q.c.to_state.in_(c.OPEN_EXPOSURE))
    keys = [setups.c.session_date, setups.c.trigger_bar_open, setups.c.occurrence_key]
    latest = dict(conn.execute(select(latest_q.c.setup_id, latest_q.c.to_state)).all())
    items, nxt = _page(conn, table=setups, key_cols=keys, where=[setups.c.run_key == rk, setups.c.id.in_(ids)],
                       cursor=cursor, limit=limit,
                       row_fn=lambda r: _setup_row(conn, r, latest))
    return _envelope(run_key=rk, record_mode=mode, items=items, next_cursor=nxt,
                     note="open research exposure: a mark is not realized P&L; reconciliation is explicit")

