/**
 * Every tier used by an ITEM must have a TIER_LABEL and a TIER_COLOR.
 *
 * REPORTED BY THE USER: "why I'm still seeing up to tier 365 on tracker page". The data was
 * deployed correctly — I verified tier 366 and 368 entries in the image, in the running
 * container, and in the JS chunk the live site actually serves. They still did not render.
 *
 * ROOT CAUSE: the render loop iterates a SEPARATE list, not the tiers present in the data:
 *
 *     const tiers = (Object.keys(TIER_LABEL).map(Number).sort((a, b) => b - a) as Tier[])...
 *     {tiers.map(tier => { const tierItems = filtered.filter(i => i.tier === tier); ... })}
 *
 * `TIER_LABEL` stopped at 365, so **18 entries** (11 in tier 366, 7 in tier 368) existed in the
 * bundle and were never visited. Silent — no error, no empty section, just absence.
 *
 * WHY TYPESCRIPT DID NOT CATCH IT: `type Tier = number`. Had it been a closed union, adding an
 * item with an unlabelled tier would have been a compile error — and this file's own history
 * shows the codebase once relied on exactly that (`the TIER_LABEL/TIER_COLOR Record<Tier,...>
 * exhaustiveness the file itself documents elsewhere`). Widening `Tier` to `number` removed that
 * protection without replacing it. This test is the replacement.
 *
 * A DIAGNOSTIC MISTAKE WORTH RECORDING: while investigating I ran
 * `grep -oc "tier:368" <minified chunk>` and read the result `1` as "only one entry made it into
 * the build". That was wrong — `grep -c` counts matching LINES, and minified JS is one enormous
 * line, so it reports 1 no matter how many matches it contains. It briefly pointed me at a
 * non-existent build problem. Count occurrences with `grep -o ... | wc -l` on minified output.
 */
import { describe, it, expect } from 'vitest';
import fs from 'fs';
import path from 'path';

const SRC = fs.readFileSync(
  path.join(__dirname, '..', 'pages', 'improvements.tsx'),
  'utf8',
);

/** Tiers referenced by at least one ITEM. */
function tiersUsedByItems(): number[] {
  return [...new Set(
    [...SRC.matchAll(/^\s*tier:\s*(\d+)\s*,/gm)].map((m) => Number(m[1])),
  )].sort((a, b) => a - b);
}

/** Tiers that have an entry in a given Record — matched at two-space indent, the file's style. */
function tiersInRecord(after: string): number[] {
  const start = SRC.indexOf(after);
  expect(start, `${after} not found`).toBeGreaterThan(-1);
  // Bound the search to the record literal that follows.
  const block = SRC.slice(start, SRC.indexOf('\n};', start));
  return [...new Set(
    [...block.matchAll(/^\s{2}(\d+):/gm)].map((m) => Number(m[1])),
  )].sort((a, b) => a - b);
}

describe('tier label/colour coverage', () => {
  it('finds a realistic number of tiers in the data', () => {
    // Guards against a regex that silently matches nothing, which would make every assertion
    // below vacuously pass — the exact failure mode that let this bug ship.
    expect(tiersUsedByItems().length).toBeGreaterThan(50);
  });

  it('every tier used by an item has a TIER_LABEL', () => {
    const labelled = new Set(tiersInRecord('const TIER_LABEL'));
    const missing = tiersUsedByItems().filter((t) => !labelled.has(t));
    expect(missing, `these tiers render NOTHING — items exist but the loop never visits them: ${missing}`)
      .toEqual([]);
  });

  it('every tier used by an item has a TIER_COLOR', () => {
    const coloured = new Set(tiersInRecord('const TIER_COLOR'));
    const missing = tiersUsedByItems().filter((t) => !coloured.has(t));
    expect(missing, `these tiers would render with an undefined colour: ${missing}`).toEqual([]);
  });

  it('specifically covers the tiers that were invisible', () => {
    const labelled = new Set(tiersInRecord('const TIER_LABEL'));
    const coloured = new Set(tiersInRecord('const TIER_COLOR'));
    for (const t of [366, 368]) {
      expect(labelled.has(t), `tier ${t} has items but no label`).toBe(true);
      expect(coloured.has(t), `tier ${t} has items but no colour`).toBe(true);
    }
  });

  it('the render loop still derives its tier list from TIER_LABEL', () => {
    // If this ever changes to derive from the items themselves, the bug becomes structurally
    // impossible and this whole test file can be reconsidered. Until then, the coupling is
    // real and must stay asserted.
    expect(SRC).toContain('Object.keys(TIER_LABEL).map(Number)');
  });
});
