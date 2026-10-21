import { expect, it } from 'vitest';
import { optionsFlowUnavailableText } from './optionsFlowStatus';

it('names each unavailable reason, and never words a server error as no activity', () => {
  const t = (reason?: string, extra = {}) => optionsFlowUnavailableText({ symbol: 'MU', available: false, reason, ...extra });
  expect(t('fetch_error')).toMatch(/server error\).*not the same as no options activity/);
  expect(t('options_feed_unavailable', { last_known_chain_date: '2026-10-20' })).toContain('last chain 2026-10-20');
  expect(t('no_volume')).toContain('No call or put volume');
  expect(t('no_options_listed')).toContain('No listed options');
  expect(t('something_new')).toBe('Unavailable (something_new).');
});
