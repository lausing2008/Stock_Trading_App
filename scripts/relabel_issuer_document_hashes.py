#!/usr/bin/env python3
"""Relabel pre-existing `sha256:` document hashes as what they always covered: the FACTS.

Rows written before the split carry one digest under a `sha256:` prefix, computed over the
extracted facts while the column was documented as byte identity. The value is correct; the
claim attached to it was not. This moves it into `facts_hash`, re-prefixes `content_hash` to
`sha256-facts:` and leaves `source_bytes_hash` NULL — because no bytes were ever stored, and a
row that cannot detect a silent edit at the source must not look like one that can.

Deliberately a one-off script and NOT a startup migration: a row-mutating migration that runs on
every boot is its own incident class (see docs/audits/2026-09-29-email-fix-closure-remediation.md).

    python3 scripts/relabel_issuer_document_hashes.py [--commit]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()

    from sqlalchemy import select
    from db import IssuerDocument, SessionLocal

    changed = 0
    with SessionLocal() as s:
        for d in s.execute(select(IssuerDocument)).scalars().all():
            h = d.content_hash or ""
            if not h.startswith("sha256:"):
                print(f"doc {d.id}: already labelled ({h[:20]}…) — skipped")
                continue
            new = "sha256-facts:" + h.split(":", 1)[1]
            print(f"doc {d.id}: content_hash {h[:20]}… -> {new[:26]}…")
            print(f"          facts_hash := same; source_bytes_hash stays NULL "
                  f"(no bytes were stored, so no source-edit detection)")
            changed += 1
            if a.commit:
                d.content_hash = new
                d.facts_hash = new
                d.source_bytes_hash = None
        if a.commit and changed:
            s.commit()
    print(f"\n{changed} row(s) {'relabelled' if a.commit else 'would be relabelled'}")
    if not a.commit:
        print("PREVIEW ONLY. Re-run with --commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
