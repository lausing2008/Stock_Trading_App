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
  expect(composition(0, 1000).kind).toBe('put_heavy');               // all puts: symmetric
  expect(composition(0, 0).kind).toBe('unmeasured');
  expect(composition(undefined, 10).kind).toBe('unmeasured');
  const c = composition(3000, 1000);
  expect([pct1(c.callShare), pct1(c.putShare)]).toEqual(['75.0%', '25.0%']);
  expect(composition(3000, 1000).label).not.toMatch(/bull|bear/i);
});

it('shows source date and coverage, and keeps premium and turnover apart', () => {
  expect(coverageLine({ chain_as_of: '2026-10-20T00:00:00', expiries_used: ['a', 'b', 'c'], expiries_attempted: ['a', 'b', 'c', 'd'], coverage_status: 'partial' }))
    .toBe('Settled session 2026-10-20 · 3 of 4 expirations · coverage partial — some expirations could not be read');
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
