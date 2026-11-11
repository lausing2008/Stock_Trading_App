import type { SqueezeCandidate } from './api';

// AUD-SQZ-DATAAGE (2026-11-10): the Prime Candidate badge read momentum from a ranking up to 90
// days old and short interest of any age. Both inputs must be current; stale rows still show
// their values, they just are not badged.
export function isPrimeCandidate(r: Pick<SqueezeCandidate, 'momentum_score' | 'short_percent_of_float' | 'ranking_is_stale' | 'is_stale'>): boolean {
  return r.momentum_score != null && r.momentum_score > 50 && r.short_percent_of_float >= 15
    && !r.ranking_is_stale && r.is_stale !== true;
}
