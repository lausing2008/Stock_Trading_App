## Recurring Issue: Improvements Page Not Showing New Tiers

**Symptom:** After adding a new tier to `improvements.tsx` (items, TIER_LABEL, TIER_COLOR, Tier type union),
the new tier items do not appear on the improvements page even after a frontend rebuild.

**Root cause (fixed 2026-06-20):** `frontend/src/pages/improvements.tsx` line ~6890 had a hardcoded
tier list `[1, 2, 3, ..., 54]` that controlled which tier sections are rendered on the page.
Adding a new tier to `TIER_LABEL`/`TIER_COLOR` and the items array had no effect because the
render loop only iterated this hardcoded list.

**Fix applied:** Replaced the hardcoded array with:
```js
const tiers = (Object.keys(TIER_LABEL).map(Number).sort((a, b) => a - b) as Tier[])
  .filter(t => filterTier === 0 || t === filterTier);
```
Now the render loop is driven by TIER_LABEL — any tier added there automatically appears.

**What to do when adding a new tier:**
1. Add `N` to the `type Tier` union
2. Add items with `tier: N`
3. Add `N: 'Tier N — ...'` to `TIER_LABEL`
4. Add `N: '#hexcolor'` to `TIER_COLOR`
5. The render loop (`tiers` variable) is now automatic — no manual update needed.
6. Rebuild frontend using the legacy (non-BuildKit) build to guarantee fresh content:
   ```
   DOCKER_BUILDKIT=0 docker build -f frontend/Dockerfile -t stockai-frontend:latest . && \
   docker compose -f docker/docker-compose.yml up -d --force-recreate frontend
   ```
   **Do NOT use** `docker compose build frontend` — BuildKit silently serves cached layers.
   `--no-cache` is NOT needed (see "Recurring Issue: Slow Frontend Builds" below).

---


## Recurring Issue: BUG-IMPROVEMENTSPAGE-STALESTATUS — Improvements Tracker's "Done" Count Could Get Stuck Forever (Fixed 2026-07-21)

**Symptom:** a user reported the `/improvements` page stuck at "1334/1371 Done" even after a
hard refresh, right after a session that shipped several fixes which should have moved the
count. The gap between the source's real done-count (1335 at the time) and the displayed
count (1334) confirmed one specific item's status was frozen in the user's browser.

**Root cause:** the page seeds a `seeded` map from every item's `defaultStatus: 'done'` field
(the source of truth for "has this actually shipped"), then merges it with `saved` (the status
map cached in `localStorage` from a previous visit). The merge was
`{ ...seeded, ...saved }` — spreading `saved` LAST means a key present in both objects takes
`saved`'s value, not `seeded`'s. So a user who visited the page once while an item was still
genuinely `'todo'` had that status cached — and even after the item was later fixed and its
`defaultStatus` flipped to `'done'` in source, the stale cached `'todo'` kept winning on every
subsequent visit, including after a hard refresh (which reloads the JS bundle but does **not**
clear `localStorage`). This directly contradicted the surrounding code comment's own stated
intent: *"Force-seed all items with defaultStatus: 'done' — implemented items are always done
regardless of any stale localStorage state."*

**Fix applied:** extracted the merge into a new pure function,
`mergeImprovementStatuses(items, saved)` in `frontend/src/lib/improvementStatuses.ts`, and
flipped the spread order to `{ ...saved, ...seeded }` — `saved` is now the base (a user's
manual status change on a genuinely `todo`/`in-progress` item still persists across visits,
unchanged from before) and `seeded` spreads last, so a `defaultStatus: 'done'` item can never
be overridden by anything cached from before it was fixed.

**Tests**: `frontend/src/lib/improvementStatuses.test.ts`, 7 cases — a stale cached `'todo'`
cannot override a done-seeded item; a stale cached `'in-progress'` also cannot; a user's
manual status on a non-done-by-default item persists (confirms the fix didn't overcorrect into
always forcing every status); an item with no `defaultStatus` at all is left to the saved map;
a brand-new done item with nothing cached seeds correctly; a mixed-items case confirms ONLY
done-seeded ids are forced while everything else keeps its saved value; and an id present in
`saved` but no longer in `ITEMS` is harmlessly carried through.

**Adversarial verification**: reverted the fix's spread order back to the original buggy
`{ ...seeded, ...saved }` and confirmed exactly 3 of 7 tests failed for the right reason (the
two direct-override cases plus the mixed-items case) before restoring the fix.

Full 70-test frontend vitest suite (up from 63) and typecheck green.

**What to check if this looks wrong**: if the "Done" count still looks stuck after a fix ships,
first confirm the fix actually flipped that item's `defaultStatus` to `'done'` in
`improvements.tsx`'s `ITEMS` array — then have the affected user open devtools and run
`localStorage.getItem('stockai:improvements:v2')` to see their raw cached map; with this fix
in place, any `'done'`-seeded id in that map should already show `'done'` regardless of what a
much older visit had cached. If it still doesn't, the bug is not this one — check
`mergeImprovementStatuses()`'s own test suite still passes first.

---

