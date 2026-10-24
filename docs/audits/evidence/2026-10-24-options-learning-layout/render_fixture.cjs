// Offline layout fixture: real component/chart/status code, synthetic feeds, no credentials.
const fs = require('fs'), vm = require('vm');
const ts = require(require.resolve('typescript', { paths: [process.cwd()] }));
const React = require(require.resolve('react', { paths: [process.cwd()] }));
const server = require(require.resolve('react-dom/server', { paths: [process.cwd()] }));
const phase = process.argv[2] || 'after';
const baseline = '1a8d4c1e9ded790a8badd2d8225d3eef613474b7';
const read = path => phase === 'before' ? fs.readFileSync('/tmp/options-layout-baseline/' + path, 'utf8') : fs.readFileSync(path, 'utf8');
function compile(path, deps = {}) {
  const sandbox = { React, exports: {}, require(name) { if (name === 'react') return React; if (Object.hasOwn(deps, name)) return deps[name]; throw new Error('Unmapped import: ' + name); } };
  vm.runInNewContext(ts.transpileModule(read(path), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.React, esModuleInterop: true } }).outputText, sandbox);
  return sandbox.exports;
}
const status = compile('src/lib/optionsFlowStatus.ts');
const chartMath = compile('src/lib/optionsTabCharts.ts');
const charts = compile('src/components/OptionsTabCharts.tsx', { '@/lib/optionsTabCharts': chartMath });
const now = Date.parse('2026-10-24T18:00:00Z');
const strikes = [500, 800, 900, 950, 1000, 1200].map((strike, i) => ({ strike, call_oi: 14506 + i * 8000, put_oi: 54171 - i * 7000 }));
const expirations = ['2026-10-30', '2026-11-06', '2026-11-20'].map((expiry, i) => ({ expiry, call_oi: 10000 + i * 3000, put_oi: 8000 + i * 2000, total_oi: 18000 + i * 5000, put_call_oi_ratio: .8, concentration_pct: 33, level: 'normal', current: true }));
const data = {
  'gamma-exposure-EXAMPLE': { available: true, source: 'unusual_whales', as_of_date: '2026-10-23', call_wall: 880, put_wall: 875, gamma_flip: 870.46, gamma_magnet: 875, oi_per_strike: strikes,
    max_pain: expirations.map((e, i) => ({ expiry: e.expiry, max_pain: 890 + i * 5 })), nope: { nope: -.273, nope_fill: .241, timestamp: '2026-10-23T19:59:00Z' } },
  'options-expirations-EXAMPLE': { available: true, expirations },
  'dark-pool-prints-EXAMPLE': { available: true, status: 'available', source: 'unusual_whales', fetched_at: '2026-10-24T17:42:00Z',
    venue_note: 'off-exchange print; a reporting-facility code does not establish which dark pool or ATS executed it',
    baseline: { status: 'ready', median: 176000, prints: 89, window_days: 14, min_prints: 20, sampling: { status: 'degraded', note: 'Captured windows only; continuous tape coverage is not established.', failed_polls: 0, saturated_windows: 64, missing_session_slots: 0, unidentified_events: 0 } },
    prints: Array.from({ length: 8 }, (_, i) => ({ executed_at_utc: `2026-10-23T19:${59 - i}:00Z`, time_status: 'ok', size: 127 + i * 100, price: 879.8 - i * .1, premium: (127 + i * 100) * 879.8, side_estimate: i % 2 ? 'buy' : 'sell', venue: 'L' })) },
};
const panel = compile('src/components/MarketPressurePanel.tsx', { '@/lib/api': { api: {} }, '@/lib/optionsFlowStatus': status,
  '@/lib/useNow': { useNow: () => now, REFRESH_MS: {} }, '@/lib/usePolledSWR': { usePolledSWR: key => ({ data: data[key], mutate() {} }) }, './OptionsTabCharts': charts });
const css = read('src/styles/globals.css');
const html = server.renderToStaticMarkup(React.createElement(panel.default, { symbol: 'EXAMPLE' }));
fs.writeFileSync(`/tmp/options-layout-${phase}.html`, `<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><style>${css}</style></head><body><main id="fixture" style="max-width:1500px;margin:auto;padding:16px"><h1 style="font-size:18px">Offline layout regression fixture — synthetic data</h1>${html}</main></body></html>`);

if (phase === 'after') {
 const calendar = compile('src/lib/optionCalendarEvidence.ts');
 const comparisonMath = compile('src/lib/optionCandidateComparison.ts', { './optionCalendarEvidence': calendar });
 const comparison = compile('src/components/OptionCandidateComparison.tsx', { '@/lib/optionCandidateComparison': comparisonMath });
 const tutorial = compile('src/pages/options-dark-pool-tutorial.tsx', {
  'next/head': () => null, 'next/link': ({ href, children, ...props }) => React.createElement('a', { href, ...props }, children),
  'next/router': { useRouter: () => ({ replace() {} }) }, '@/lib/auth': { getSession: () => null, hasAdvancedAccess: () => false },
  '@/components/OptionCandidateComparison': comparison,
 });
 const lesson = server.renderToStaticMarkup(React.createElement(tutorial.OptionsDarkPoolTutorialContent));
 fs.writeFileSync('/tmp/options-tutorial-preview.html', `<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><style>${css}</style></head><body>${lesson}</body></html>`);
}
