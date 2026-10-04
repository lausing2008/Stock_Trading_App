#!/usr/bin/env python3
"""Point a superseded-in-scope report at the report that describes the current event.

TWO KINDS, AND THE DIFFERENCE MATTERS.

  --kind wrong-event  The report analysed the wrong event. Its conclusions are about something
                      other than what it claimed to be about.

  --kind coverage     The report's CONTENTS remain a valid historical record. What was wrong is
                      only its implicit claim to be the LATEST available results — a later
                      release existed, or has since been ingested. Micron's June quarter really
                      did report those figures; nothing about them was replaced or corrected.
                      Saying "this report was corrected" would be false, and would quietly
                      retract accurate history.

Both write a forward pointer. Neither edits the superseded payload.

WHY THIS IS A SEPARATE, EXPLICIT ACT. `supersedes_id` links versions of one subject. When a
report turns out to describe a different event entirely — MU report #11 presented the June
quarter as the latest results while the 30 September release already existed — the corrected
report has its own subject key and is version 1 of it. Nothing links the two, and a reader of
the old one has no way to learn it is wrong.

This writes a pointer and a reason. It NEVER edits the superseded payload: a frozen report
whose contents can change is not evidence of what was known at the time.

    python3 scripts/record_report_correction.py --stale 11 --corrected-by 12 \
        --actor sing --reason "describes the June quarter; the September release is #12" [--commit]
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stale", type=int, required=True)
    ap.add_argument("--corrected-by", type=int, required=True)
    ap.add_argument("--actor", required=True)
    ap.add_argument("--reason", required=True)
    ap.add_argument("--kind", choices=("wrong-event", "coverage"), required=True,
                    help="wrong-event: the analysis was of the wrong event. "
                         "coverage: the contents stand; only the claim to be the latest "
                         "available results was superseded.")
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()

    from db import IntelligenceReport, SessionLocal
    with SessionLocal() as s:
        stale = s.get(IntelligenceReport, a.stale)
        newer = s.get(IntelligenceReport, a.corrected_by)
        if stale is None or newer is None:
            print("report not found"); return 2
        if stale.id == newer.id:
            print("a report cannot correct itself"); return 2
        if stale.symbol != newer.symbol:
            print(f"different issuers: {stale.symbol} vs {newer.symbol}"); return 2
        if stale.subject_key == newer.subject_key:
            print("same subject_key — this is a VERSION, which supersedes_id already records, "
                  "not a cross-subject correction"); return 2
        if newer.generated_at and stale.generated_at and newer.generated_at < stale.generated_at:
            print("the correcting report is older than the one it corrects"); return 2

        kind = a.kind.replace("-", "_")
        plan = {
            "kind": kind,
            "headline": ("This report analysed a different event than the one now understood "
                         "to be current."
                         if kind == "wrong_event" else
                         "A later release has since been ingested. The results below remain a "
                         "valid record of their own period — they were not replaced or found "
                         "to be wrong. What no longer holds is this report's standing as the "
                         "latest available results."),
            "stale": {"id": stale.id, "subject_key": stale.subject_key,
                      "generated_at": str(stale.generated_at)},
            "corrected_by": {"id": newer.id, "subject_key": newer.subject_key,
                             "generated_at": str(newer.generated_at)},
            "actor": a.actor, "reason": a.reason,
            "payload_change": "none — the superseded snapshot's contents are not edited",
        }
        for k, v in plan.items():
            print(f"{k}: {v}")
        if not a.commit:
            print("\nPREVIEW ONLY. Re-run with --commit to write the pointer.")
            return 0
        stale.corrected_by_id = newer.id
        stale.correction = {**plan,
                            "recorded_at": datetime.now(timezone.utc).isoformat()}
        s.commit()
        print(f"\nrecorded: report #{stale.id} now points forward to #{newer.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
