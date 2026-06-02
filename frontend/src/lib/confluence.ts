type MinSignal = { signal: string; confidence: number };

type MinRow = {
  score?: number | null;
  technical?: number | null;
  momentum?: number | null;
};

export type ConfluenceGrade = {
  label: 'Strong' | 'Good' | 'Moderate' | 'Weak';
  color: string;
  maxPositionPct: string;
  description: string;
};

/**
 * 0–100 confluence score using data available on the Rankings / Opportunities pages.
 *
 * Weights:
 *   AI signal direction × confidence  35%
 *   K-Score composite                 30%
 *   Technical sub-score               20%
 *   Momentum sub-score                15%
 */
export function confluenceScore(row: MinRow, signal?: MinSignal): number {
  const aiDir =
    signal?.signal === 'BUY'  ? 100 :
    signal?.signal === 'HOLD' ? 50  :
    signal?.signal === 'WAIT' ? 25  : 0;
  const ai = aiDir * (signal?.confidence ?? 50) / 100;
  return Math.round(
    ai               * 0.35 +
    (row.score      ?? 50) * 0.30 +
    (row.technical  ?? 50) * 0.20 +
    (row.momentum   ?? 50) * 0.15,
  );
}

/**
 * Full confluence score including analyst consensus (for the stock detail page
 * where fundamentals are already loaded).
 *
 * @param recommendationMean yfinance value: 1.0 = Strong Buy → 5.0 = Sell
 *
 * Weights:
 *   AI signal direction × confidence  30%
 *   K-Score composite                 25%
 *   Analyst consensus                 20%
 *   Technical sub-score               15%
 *   Momentum sub-score                10%
 */
export function confluenceScoreFull(
  row: MinRow,
  signal: MinSignal | undefined,
  recommendationMean: number | null,
): number {
  const aiDir =
    signal?.signal === 'BUY'  ? 100 :
    signal?.signal === 'HOLD' ? 50  :
    signal?.signal === 'WAIT' ? 25  : 0;
  const ai = aiDir * (signal?.confidence ?? 50) / 100;
  const analyst = recommendationMean != null
    ? Math.max(0, Math.min(100, (5 - recommendationMean) / 4 * 100))
    : 50;
  return Math.round(
    ai               * 0.30 +
    (row.score      ?? 50) * 0.25 +
    analyst          * 0.20 +
    (row.technical  ?? 50) * 0.15 +
    (row.momentum   ?? 50) * 0.10,
  );
}

export function confluenceGrade(score: number): ConfluenceGrade {
  if (score >= 80) return {
    label: 'Strong', color: '#4ade80', maxPositionPct: '8–10%',
    description: 'All signals align — high-conviction entry zone',
  };
  if (score >= 65) return {
    label: 'Good', color: '#86efac', maxPositionPct: '5–7%',
    description: 'Most signals agree — size normally',
  };
  if (score >= 50) return {
    label: 'Moderate', color: '#fbbf24', maxPositionPct: '2–4%',
    description: 'Mixed signals — reduce size, wait for confirmation',
  };
  return {
    label: 'Weak', color: '#f87171', maxPositionPct: 'Avoid',
    description: 'Signals conflict — no entry recommended',
  };
}
