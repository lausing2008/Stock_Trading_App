"""One-time migration: collapse each congress member's spelling variants onto one canonical name.

WHY THIS EXISTS. congress_trades is fed by two sources that spell the same person differently —
UW's `name` ("Ro Khanna"), the kadoa feed's ("Rohit Khanna"), and the honorific `reporter` form
("Hon. David J. Taylor"). politician_name is part of the uq_congress_trade key, so each spelling
became its own ranked trader: the live leaderboard showed Khanna twice, at +5.26% (n=23) and
+3.30% (n=55). AUD-UWCONGRESS-NAMEMERGE fixed this for rows ingested from now on; this script
repairs the rows already stored.

Run it with no flags first. It prints exactly what it would change and writes nothing:

    python scripts/migrate_canonicalize_congress_names.py
    python scripts/migrate_canonicalize_congress_names.py --apply

COLLISIONS. Renaming a variant onto its canonical form can collide with a row that already has
the canonical name and the same (ticker, trade_date, transaction_type) — that is the SAME trade
arriving from both feeds. The canonical-named row is kept, because it comes from the source that
carries direction, disclosure date and amount; the variant is deleted. Nothing is merged across
different trades, only exact duplicates of one.

WHAT THIS DOES NOT DO. It never invents a canonicalisation of its own. Every rename comes from
canonicalize_politician_name() against UW's live roster, which refuses to merge anything
ambiguous — so a member absent from the roster, or one whose surname is shared in-chamber with a
compatible given name, is left exactly as it is.
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, "/app")

from sqlalchemy import text  # noqa: E402

from common.uw_congress import (  # noqa: E402
    canonicalize_politician_name as canon,
    get_congress_roster,
)
from shared.db import SessionLocal  # noqa: E402


def build_plan(session, roster) -> list[tuple[str, str, str]]:
    rows = session.execute(
        text("SELECT DISTINCT politician_name, chamber FROM congress_trades")
    ).all()
    plan = []
    for r in rows:
        new = canon(r.politician_name, r.chamber, roster)
        if new and new != r.politician_name:
            plan.append((r.politician_name, r.chamber, new))
    return sorted(plan)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = ap.parse_args()

    roster = get_congress_roster()
    if not roster:
        print("ABORT: roster unavailable — without it there is no authority to rename against.")
        return 1
    # The roster is Redis-cached for 24h. A cache written before `canonical_name` was added
    # returns entries without it, and every rename then silently resolves to a no-op — which is
    # exactly what happened on the first run of this migration.
    if not any(v.get("canonical_name") for v in roster.values()):
        print("ABORT: roster has no canonical_name (stale cache?). Clear "
              "'stockai:uw:congress:roster' and retry.")
        return 1
    print(f"roster: {len(roster)} members")

    with SessionLocal() as s:
        plan = build_plan(s, roster)
        print(f"renames planned: {len(plan)}")
        for old, ch, new in plan:
            print(f"   {old[:38]:<40} [{ch}] -> {new}")
        if not plan:
            print("nothing to do")
            return 0
        if not args.apply:
            print("\nDRY RUN — nothing written. Re-run with --apply to commit.")
            return 0

        deleted = renamed = 0
        for old, ch, new in plan:
            d = s.execute(
                text("""
                    DELETE FROM congress_trades t
                    WHERE t.politician_name = :old AND EXISTS (
                      SELECT 1 FROM congress_trades u
                      WHERE u.politician_name = :new AND u.ticker = t.ticker
                        AND u.trade_date = t.trade_date
                        AND u.transaction_type = t.transaction_type)
                """),
                {"old": old, "new": new},
            )
            u = s.execute(
                text("UPDATE congress_trades SET politician_name = :new WHERE politician_name = :old"),
                {"old": old, "new": new},
            )
            deleted += d.rowcount
            renamed += u.rowcount
        s.commit()
        print(f"\nduplicate rows deleted: {deleted}")
        print(f"rows renamed:           {renamed}")
        print(f"distinct names now:     {s.execute(text('SELECT count(DISTINCT politician_name) FROM congress_trades')).scalar()}")
        print(f"total rows:             {s.execute(text('SELECT count(*) FROM congress_trades')).scalar()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
