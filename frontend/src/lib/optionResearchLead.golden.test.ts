// Golden cases shared with the backend replay (services/market-data/tests/fixtures/
// option_research_lead_golden.json). This TypeScript ranking is the REFERENCE; the Python port in
// options_candidate_study.research_lead must reproduce `expected` exactly. If the ranking changes
// on purpose, regenerate with UPDATE_GOLDEN=1 and the backend golden test will flag the port.
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { optionResearchLead } from './optionResearchLead';
import { EMPTY_OPTION_PREFERENCES } from './optionSearchPreferences';

const PATH = resolve(__dirname, '../../../services/market-data/tests/fixtures/option_research_lead_golden.json');
type Case = { name: string; key: string; today: string; matrix: unknown; review: unknown; holding_days?: number | null;
  expected?: { index: number | null; option_symbol: string | null } | null };

function run(c: Case) {
  const lead = optionResearchLead(c.key as never, c.matrix as never, c.review as never, c.today,
    'holding_days' in c ? c.holding_days : undefined, EMPTY_OPTION_PREFERENCES);
  return lead ? { index: lead.index, option_symbol: lead.strategy.legs[0].option_symbol ?? null } : null;
}

describe('research-lead golden cases', () => {
  const golden = JSON.parse(readFileSync(PATH, 'utf8')) as { _note: string; cases: Case[] };
  if (process.env.UPDATE_GOLDEN) {
    golden.cases = golden.cases.map(c => ({ ...c, expected: run(c) }));
    writeFileSync(PATH, JSON.stringify(golden, null, 1) + '\n');
  }
  for (const c of golden.cases) {
    it(c.name, () => {
      expect('expected' in c).toBe(true);
      expect(run(c)).toEqual(c.expected);
    });
  }
});
