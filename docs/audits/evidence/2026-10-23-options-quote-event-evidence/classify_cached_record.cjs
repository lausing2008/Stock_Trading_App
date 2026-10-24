// Run from frontend. Reuse the exact reviewed TypeScript module without network access.
const fs = require('fs');
const vm = require('vm');
const ts = require(require.resolve('typescript', { paths: [process.cwd()] }));
const cache = JSON.parse(fs.readFileSync('../docs/audits/evidence/2026-10-23-options-quote-event-evidence/cache.json', 'utf8'));
const source = fs.readFileSync('src/lib/optionCalendarEvidence.ts', 'utf8');
const sandbox = { exports: {} };
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, sandbox);
const now = Date.parse(cache.checked_at);
const context = cache.calendar ? { symbol: cache.symbol, retrievedAt: cache.calendar.fetched_at, earningsDate: cache.calendar.next_earnings_date, exDividendDate: cache.calendar.ex_dividend_date } : undefined;
const calendar = sandbox.exports.reviewOptionCalendar(context, cache.symbol, now);
const today = new Intl.DateTimeFormat('sv-SE', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(now));
const previous = JSON.parse(fs.readFileSync('../docs/audits/evidence/2026-10-23-four-strategy-expirations/tsm.json', 'utf8'));
const expiries = [...new Set(Object.values(previous.holding_searches).flatMap(v => v.strategies.covered_call.searched_expiries))].sort();
const windows = expiries.map(expiry => ({ expiry,
  earnings: sandbox.exports.optionEventWindow(calendar, calendar.earningsDate, expiry, today, 14),
  ex_dividend: sandbox.exports.optionEventWindow(calendar, calendar.exDividendDate, expiry, today, 14),
}));
if (cache.record_present && !calendar.current) throw new Error('Actual cached record failed retrieval validation; inspect evidence rather than assuming freshness');
if (!windows.some(w => w.earnings.code === 'after_expiry') || !windows.some(w => w.earnings.code === 'before_expiry')) throw new Error('Expected both reported earnings timing branches in these archived expiration examples');
console.log('CALENDAR_CLASSIFICATION_JSON=' + JSON.stringify({ checked_at: cache.checked_at, symbol: cache.symbol, calendar, today, planned_holding_days: 14, expiry_source: 'preceding read-only TSM archive witness; policy examples, not a new market search', windows }));
