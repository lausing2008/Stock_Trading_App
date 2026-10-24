// Run from frontend using installed dependencies; no network or database access.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const ts = require(require.resolve('typescript', { paths: [process.cwd()] }));
const React = require(require.resolve('react', { paths: [process.cwd()] }));
const ReactDOMServer = require(require.resolve('react-dom/server', { paths: [process.cwd()] }));
function compile(file, modules = {}) {
  const sandbox = { exports: {}, require(name) { if (name === 'react') return React; if (Object.hasOwn(modules, name)) return modules[name]; throw new Error('Unexpected import: ' + name); } };
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.React, esModuleInterop: true } }).outputText, sandbox);
  return sandbox.exports;
}
const calendar = compile('src/lib/optionCalendarEvidence.ts');
const helpers = compile('src/lib/optionCandidateComparison.ts', { './optionCalendarEvidence': calendar });
const component = compile('src/components/OptionCandidateComparison.tsx', { '@/lib/optionCandidateComparison': helpers });
const sample = JSON.parse(fs.readFileSync('../docs/audits/evidence/2026-10-24-options-candidate-comparison/tsm.json', 'utf8'));
const results = {};
for (const [key, data] of Object.entries(sample.comparisons)) {
  const before = JSON.stringify(data.alternatives);
  const orders = {};
  for (const sort of ['server', 'spread', 'oi', 'capital', 'expiry']) {
    const rows = helpers.compareCandidateRows(data.alternatives, sort, sample.today);
    assert.strictEqual(rows.length, data.alternatives.length);
    for (const row of rows) assert.strictEqual(row.strategy, data.alternatives[row.sourceIndex]);
    assert.strictEqual(JSON.stringify(data.alternatives), before);
    orders[sort] = rows.map(row => ({ source_index: row.sourceIndex, contract: row.strategy.legs[0].option_symbol }));
  }
  const html = ReactDOMServer.renderToStaticMarkup(React.createElement(component.default, {
    original: data.original ?? undefined, originalStrike: data.original_reference.strike, originalExpiry: data.original_expiry,
    originalSymbol: data.original_reference.option_symbol, alternatives: data.alternatives,
    strategyKey: key, today: sample.today, selectedIndex: null, onSelect() { throw new Error('Rendering must not select'); },
  }));
  assert(html.includes('Shown spread (rounded)'));
  assert(html.includes('Sort returned alternatives'));
  for (const alt of data.alternatives) assert(html.includes(alt.legs[0].option_symbol));
  if (!data.original) assert(html.includes('Payoff unavailable') && html.includes(data.original_reference.option_symbol));
  results[key] = { returned_count: data.alternatives.length, original_priced: !!data.original, static_table_verified: true, source_mapping_verified: true, orders };
}
console.log(JSON.stringify({ symbol: sample.symbol, source_session: sample.source_session, sample_checked_at: sample.checked_at, method: 'reviewed TypeScript sort and React static-table replay on read-only archive sample; not interactive browser acceptance', results }, null, 2));
