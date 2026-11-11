// AUD-OPT-IVRANK-ALIGN (2026-11-10): the one definition of "rich" and "cheap" IV rank (0-100, where
// today's implied volatility sits in its own trailing year). Must equal IV_RANK_RICH/IV_RANK_CHEAP in
// services/market-data/src/services/options_strategies.py; a backend test compares the two.
// A convention, not a measured edge.
export const IV_RANK_RICH = 70;
export const IV_RANK_CHEAP = 30;

export function ivRankRead(rank: number | null | undefined): 'options relatively expensive' | 'options relatively cheap' | 'mid-range' | null {
  if (typeof rank !== 'number' || !Number.isFinite(rank) || rank < 0 || rank > 100) return null;
  return rank >= IV_RANK_RICH ? 'options relatively expensive' : rank <= IV_RANK_CHEAP ? 'options relatively cheap' : 'mid-range';
}
