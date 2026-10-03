"""Persisting report snapshots: immutable, versioned, idempotent on inputs.

THREE PROPERTIES, AND WHY EACH IS A STORAGE CONCERN RATHER THAN A CALLER'S DISCIPLINE:

  IMMUTABLE — a revision inserts a new row pointing at the one it supersedes. Nothing here
  updates a payload, because the pre-earnings freeze is only worth something if it cannot be
  edited after the results land, and a caller that "just updates" would destroy that silently.

  VERSIONED — version is derived from the subject's existing rows inside the same transaction,
  not passed in, so two concurrent generations cannot both claim version 3.

  IDEMPOTENT ON INPUTS — identical inputs return the EXISTING report instead of writing a
  near-duplicate. Keyed on the input fingerprint rather than elapsed time: a report regenerated
  a minute later from unchanged evidence is the same report, and a report regenerated after the
  evidence moved is a genuine new version however little time passed.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, func

from db import IntelligenceReport
from intelligence.report_contract import CONTRACT_VERSION


def _payload(fields, evidence, meta) -> dict:
    return {
        "fields": {k: v.to_dict() for k, v in fields.items()},
        "meta": {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in meta.items()},
    }


def existing_for_fingerprint(session, *, subject_key: str, fingerprint: str,
                             user_id: int | None):
    """The most recent report for this subject built from exactly these inputs, if any."""
    return session.execute(
        select(IntelligenceReport)
        .where(IntelligenceReport.subject_key == subject_key,
               IntelligenceReport.input_fingerprint == fingerprint,
               IntelligenceReport.user_id.is_(None) if user_id is None
               else IntelligenceReport.user_id == user_id)
        .order_by(IntelligenceReport.version.desc()).limit(1)).scalars().first()


def latest(session, *, subject_key: str, report_type: str | None = None,
           user_id: int | None = None):
    q = select(IntelligenceReport).where(IntelligenceReport.subject_key == subject_key)
    if report_type:
        q = q.where(IntelligenceReport.report_type == report_type)
    q = q.where(IntelligenceReport.user_id.is_(None) if user_id is None
                else IntelligenceReport.user_id == user_id)
    return session.execute(q.order_by(IntelligenceReport.version.desc()).limit(1)).scalars().first()


def frozen_pre_report(session, *, subject_key: str, before: datetime):
    """The pre-earnings report that was FROZEN before a cutoff — the accountability baseline.

    `cutoff_at < before` is the condition, not `generated_at`: a report generated after the
    release from evidence complete only to an earlier cutoff would still be a reconstruction,
    and the whole point is that a post-release report is scored against something written when
    the outcome was genuinely unknown. Both timestamps must precede the release for a row to
    qualify.
    """
    return session.execute(
        select(IntelligenceReport)
        .where(IntelligenceReport.subject_key == subject_key,
               IntelligenceReport.report_type == "pre_earnings",
               IntelligenceReport.cutoff_at < before,
               IntelligenceReport.generated_at < before)
        .order_by(IntelligenceReport.version.desc()).limit(1)).scalars().first()


def save(session, fields, evidence, meta, coverage_counts, *, user_id: int | None = None):
    """Insert a snapshot, or return the identical existing one. Returns (report, created)."""
    subject_key = meta["subject_key"]
    fingerprint = meta["fingerprint"]

    same = existing_for_fingerprint(session, subject_key=subject_key,
                                    fingerprint=fingerprint, user_id=user_id)
    if same is not None:
        return same, False

    prior = latest(session, subject_key=subject_key,
                   report_type=meta["report_type"], user_id=user_id)
    report = IntelligenceReport(
        report_type=meta["report_type"],
        subject_key=subject_key,
        symbol=meta.get("symbol"),
        market=meta.get("market"),
        version=(prior.version + 1) if prior else 1,
        supersedes_id=prior.id if prior else None,
        pre_report_id=meta.get("pre_report_id"),
        status=meta["status"],
        stage=meta.get("stage"),
        contract_version=CONTRACT_VERSION,
        policy_version=meta.get("policy_version", "1"),
        generated_at=datetime.utcnow(),
        cutoff_at=meta["cutoff_at"],
        input_fingerprint=fingerprint,
        payload=_payload(fields, evidence, meta),
        evidence={"records": evidence},
        coverage=coverage_counts,
        user_id=user_id,
    )
    session.add(report)
    session.flush()
    # The superseded row keeps its payload exactly as issued; only its status is marked, so
    # history stays readable rather than being rewritten.
    if prior is not None and prior.status != "superseded":
        prior.status = "superseded"
    session.commit()
    return report, True


def history(session, *, subject_key: str, user_id: int | None = None, limit: int = 20):
    q = select(IntelligenceReport).where(IntelligenceReport.subject_key == subject_key)
    q = q.where(IntelligenceReport.user_id.is_(None) if user_id is None
                else IntelligenceReport.user_id == user_id)
    return list(session.execute(
        q.order_by(IntelligenceReport.version.desc()).limit(limit)).scalars().all())


def diff(previous: IntelligenceReport | None, current: IntelligenceReport) -> dict:
    """What changed since the previous report — the templates' "what changed" section.

    Compares FIELD STATE as well as value, because a dimension going from OK to STALE is a
    change a reader needs even though the number did not move.
    """
    if previous is None:
        return {"first_report": True, "changed": [], "note": "no previous report for this subject"}
    before = (previous.payload or {}).get("fields", {})
    after = (current.payload or {}).get("fields", {})
    changed = []
    for key in sorted(set(before) | set(after)):
        b, a = before.get(key), after.get(key)
        if b is None:
            changed.append({"field": key, "change": "added", "to": a}); continue
        if a is None:
            changed.append({"field": key, "change": "removed", "from": b}); continue
        if b.get("state") != a.get("state"):
            changed.append({"field": key, "change": "state",
                            "from": b.get("state"), "to": a.get("state")})
        elif b.get("value") != a.get("value"):
            changed.append({"field": key, "change": "value",
                            "from": b.get("value"), "to": a.get("value")})
    return {"first_report": False, "from_version": previous.version,
            "to_version": current.version, "changed": changed}
