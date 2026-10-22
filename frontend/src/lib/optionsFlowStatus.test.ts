import { expect, it } from 'vitest';
import { optionsFlowUnavailableText } from './optionsFlowStatus';

it('names each unavailable reason, and never words a server error as no activity', () => {
  const t = (reason?: string, extra = {}) => optionsFlowUnavailableText({ symbol: 'MU', available: false, reason, ...extra });
  expect(t('fetch_error')).toMatch(/server error\).*not the same as no options activity/);
  expect(t('options_feed_unavailable', { last_known_chain_date: '2026-10-20' })).toContain('last chain 2026-10-20');
  expect(t('no_volume')).toContain('No call or put volume');
  expect(t('chain_read_failed')).toMatch(/data failure, not an absence of activity/);
  expect(t('no_options_listed')).toContain('No listed options');
  expect(t('something_new')).toBe('Unavailable (something_new).');
});

import { composition, coverageLine, premiumText, turnoverText, requestFailedText, pct1 } from './optionsFlowStatus';

it('classifies composition from exact volumes with the agreed C/P cut points', () => {
  expect(composition(1300, 1000).kind).toBe('call_heavy');           // exactly 1.3
  expect(composition(1296, 1000).kind).toBe('mixed');                // 1.296 displays as "1.30" but is mixed
  expect(composition(1296, 1000).ratioText).toBe('C/P 1.30');
  expect(composition(800, 1000).kind).toBe('put_heavy');             // exactly 0.8
  expect(composition(801, 1000).kind).toBe('mixed');
  expect(composition(1000, 0)).toMatchObject({ kind: 'call_heavy', ratioText: 'C/P undefined (no puts)' });
  expect(composition(0, 1000)).toMatchObject({ kind: 'put_heavy', ratioText: 'C/P 0 (no calls)' });   // all puts: symmetric
  expect(composition(0, 0).kind).toBe('unmeasured');
  expect(composition(undefined, 10).kind).toBe('unmeasured');
  const c = composition(3000, 1000);
  expect([pct1(c.callShare), pct1(c.putShare)]).toEqual(['75.0%', '25.0%']);
  expect(composition(3000, 1000).label).not.toMatch(/bull|bear/i);
});

it('shows source date and coverage, and keeps premium and turnover apart', () => {
  expect(coverageLine({ chain_as_of: '2026-10-20T00:00:00', expiries_used: ['a', 'b', 'c'], expiries_attempted: ['a', 'b', 'c', 'd'], coverage_status: 'partial' }))
    .toBe('Settled session 2026-10-20 · 3 of 4 expirations (a, b, c) · coverage partial — some expirations could not be read');
  expect(coverageLine({})).toContain('Settled session unknown');
  expect(premiumText({ premium: null, traded_premium: null })).toBe('unavailable');
  expect(premiumText({ traded_premium: 1_250_000 })).toBe('$1.3M');
  expect(turnoverText({ estimated_turnover: 48_000 })).toBe('≈$48K');
  expect(turnoverText({ estimated_turnover: null })).toBe('unavailable');
});

it('words every request failure as unknown, never as empty', () => {
  expect(requestFailedText(new Error('500 Internal Server Error'))).toMatch(/HTTP 500.*unknown, not empty/);
  expect(requestFailedText(new Error('Request timed out'))).toMatch(/timed out/);
  expect(requestFailedText(new Error('Unauthorized'))).toMatch(/not authorised/);
  expect(requestFailedText(new TypeError('Failed to fetch'))).toMatch(/network/);
});

import { partialSum } from './optionsFlowStatus';
it('never presents a partial premium sum as complete', () => {
  expect(partialSum([600_000, null, undefined])).toEqual({ sum: 600_000, known: 1, total: 3 });
  expect(partialSum([null, null])).toEqual({ sum: 0, known: 0, total: 2 });
});

// ── Options review B1 + shared composition rule ────────────────────────────────────────────
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { coverageStatusText, tradeableExpiries, servedExpiryIsPast, expiredSinceLine } from './optionsFlowStatus';

const shared = JSON.parse(readFileSync(resolve(__dirname, '../../../services/market-data/tests/fixtures/options_composition_cases.json'), 'utf8')) as
  { cases: { call: number; put: number; kind: string; why: string }[] };

it.each(shared.cases)('matches the backend composition rule: $why', ({ call, put, kind }) => {
  expect(composition(call, put).kind).toBe(kind);
});

it('words the archive completeness evidence instead of a bare "unverified"', () => {
  expect(coverageStatusText('complete')).toMatch(/complete \(archive capture reached its last page\)/);
  expect(coverageStatusText('incomplete')).toMatch(/did not finish/);
  expect(coverageStatusText('unverified_legacy_cap')).toMatch(/before completeness was recorded/);
  expect(coverageStatusText(undefined)).toBe('unknown');
});

it('offers only the server\'s current expiries, so a same-day contract is gone after the close', () => {
  const dte = () => 0;                                         // client date math still says "today"
  expect(tradeableExpiries({ expiries: ['2026-10-21', '2026-10-23'], current_expiries: ['2026-10-23'] }, dte)).toEqual(['2026-10-23']);
  expect(tradeableExpiries({ expiries: ['2026-10-21', '2026-10-23'] }, e => (e === '2026-10-21' ? -1 : 2))).toEqual(['2026-10-23']);
  expect(servedExpiryIsPast({ expiry: '2026-10-21', expiry_is_current: false }, dte)).toBe(true);
  expect(servedExpiryIsPast({ expiry: '2026-10-23', expiry_is_current: true }, () => -5)).toBe(false);
});

it('names expirations that expired since the session as historical evidence only', () => {
  expect(expiredSinceLine({ historical_expiries: ['2026-10-21', '2026-10-23'], current_expiries: ['2026-10-23'] }))
    .toBe('1 of 2 expirations (2026-10-21) have expired since that session — historical evidence only, not current trade candidates.');
  expect(expiredSinceLine({ historical_expiries: ['2026-10-23'], current_expiries: ['2026-10-23'] })).toBeNull();
  expect(expiredSinceLine(undefined)).toBeNull();
});

it('says why a calculator is empty when every archived expiration has stopped trading', async () => {
  const { calculatorUnavailableReason } = await import('./optionsFlowStatus');
  expect(calculatorUnavailableReason({ available: true, current_expiries: [] }, [])).toBe('every expiration in the archived session has stopped trading');
  expect(calculatorUnavailableReason({ available: true, current_expiries: ['2026-10-23'] }, ['2026-10-23'])).toBeUndefined();
  expect(calculatorUnavailableReason({ available: false, reason: 'fetch_error' }, [])).toBe('fetch_error');
  expect(calculatorUnavailableReason(undefined, [])).toBeUndefined();
});

it('renders an after-midnight ET print as 00:xx, never 24:xx', async () => {
  const { printTimeText } = await import('./optionsFlowStatus');
  expect(printTimeText({ executed_at_utc: '2026-10-22T04:05:00Z', time_status: 'ok', age_minutes: 0 })).toBe('Oct 22, 00:05 ET · 0 min ago');
});
