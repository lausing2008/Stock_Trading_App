import { expect, it } from 'vitest';
import { parseWorkspaceLayout } from './stockWorkspaceLayout';

it('migrates original layouts and round-trips portable preferences', () => {
  const original = parseWorkspaceLayout({ order: ['chart', 'chart'], closed: { chart: true } });
  expect(original).toEqual({ order: ['chart'], closed: { chart: true }, hidden: {}, widths: {} });
  const updated = { ...original, hidden: { research: true }, widths: { chart: 'full' } };
  expect(parseWorkspaceLayout(JSON.parse(JSON.stringify({ version: 1, ...updated })))).toEqual(updated);
});

it('rejects unsupported, excessive and malformed files before applying preferences', () => {
  for (const raw of [null, [], { version: 2, order: [] }, { order: ['../chart'] },
    { order: Array(101).fill('chart') }, { order: [], widths: { chart: '4000px' } },
    { order: [], hidden: { chart: 'true' } }, { order: [], closed: [] },
    JSON.parse('{"order":[],"closed":{"__proto__":true}}')]) {
    expect(() => parseWorkspaceLayout(raw)).toThrow();
  }
});
