import type { OptionSnapshot } from '@/lib/api';
export const snapshot: OptionSnapshot = {
  id: 'a'.repeat(32), symbol: 'MU', source: 'unusual_whales_current_contracts',
  capture_started_at: '2026-10-24T14:59:50Z', captured_at: '2026-10-24T15:00:00Z',
  expires_at: '2026-10-24T15:10:00Z', refresh_after: '2026-10-24T15:01:00Z',
  stock_reference: 877.55, stock_observed_at: '2026-10-23T20:00:00Z', stock_market_time: 'regular',
  source_session: '2026-10-23', source_session_basis: 'latest trade session proxy',
  quote_observation_time_available: false, pages: 23, contracts: 11126, rejected_contracts: 1,
  pagination_complete: true, expiries: ['2026-11-20'], limitations: 'Trade times are not quote times; verify broker quotes.',
};
