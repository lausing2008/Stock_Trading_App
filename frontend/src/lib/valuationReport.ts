import type { QvEvaluation } from './api';

export type ValuationMetric = { value: number | null; state: string; reason: string | null; unit?: string | null;
  source?: string | null; as_of?: string | null; basis?: string | null };
export type ValuationScenario = { fair_value: number; growth_pct: number; discount_rate_pct: number;
  terminal_growth_pct: number; terminal_value_share_pct: number; enterprise_value: number; equity_value: number };
export type CandidateReview = {
  version: number; reviewed_at: string; author: string; decision: string; status: string; summary: string;
  sources: { id: string; title: string; url: string; published_at: string; period_end: string }[];
  findings: { claim: string; source_ids: string[]; counterevidence: string }[];
  thesis: string[]; monitor: string[]; invalidation: string[]; unresolved: string[]; qualification_blockers: string[];
  sensitivity?: { method: string; period_end: string; cashflow: number; diluted_shares: number;
    scenarios: Record<string, Omit<ValuationScenario, 'enterprise_value'>>; limitations: string[] };
};
export type ValuationRow = {
  symbol: string; name: string; sector: string; industry?: string | null; country?: string | null;
  provider?: string; eps_note?: string | null; listing_basis?: string | null;
  tracked: boolean; classification: string; qualified: boolean; exclusions: string[]; blockers: string[];
  metrics: Record<string, ValuationMetric>; valuation: { state: string; reason?: string; method?: string;
    period_end?: string; uncertainty?: string; scenarios?: Record<string, ValuationScenario>;
    inputs?: Record<string, number>; limitations?: string[] };
  analyst: { mean: number | null; median: number | null; low: number | null; high: number | null;
    analyst_count: number | null; consensus: string | null; state: string; note: string; upside_pct: number | null };
  analyst_actions: { date: string; firm: string; rating: string; action: string; target?: number | null }[];
  risk_flags: { kind: string; text: string; date?: string | null }[];
  quality: QvEvaluation; thesis: string[]; research_checks: string[]; catalyst_note: string;
  candidate_review?: CandidateReview | null;
  uw: { si_float?: number | null; days_to_cover?: number | null; fee_rate?: number | null; market_date?: string | null } | null;
  errors: string[]; fetched_at: string | null; price_session: string | null; source_url?: string;
  peer_pe: { count: number; median: number | null; note: string };
};
export type ValuationReport = {
  id: number; version: number; supersedes_id: number | null; generated_at: string; cutoff_at: string;
  status: string; valuation: { rows: ValuationRow[]; counts: Record<string, number>; price_session: string;
    universe: string; discovered: number; sampled: number; sample_limit: number; queries: string[];
    queries_failed: string[]; uw_discovery?: boolean; policy_version: string; notes: string[]; horizon: string;
    changes: { symbol: string; kind: string; before: string | null; after: string }[]; comparison_note: string };
};
export type ValuationJob = { job_id: string; state: string; stage?: string; report_id?: number; error?: string; created?: boolean };
export type ValuationHistory = { id: number; version: number; generated_at: string; price_session: string };
export type ValuationFilter = 'all' | 'undervalued' | 'overvalued' | 'qualified' | 'analyst_risk' | 'reviewed';

export const classificationLabel = (value: string) => ({
  undervalued: 'Below model value', overvalued: 'Above model value', near_fair_value: 'Near model value',
  insufficient_evidence: 'Insufficient evidence',
}[value] || value.replaceAll('_', ' '));

export function selectValuationRows(rows: ValuationRow[], opts: {
  filter: ValuationFilter; search: string; sector: string; sort: string; ascending: boolean;
}) {
  const search = opts.search.trim().toLowerCase();
  return rows.filter(r => (!search || `${r.symbol} ${r.name}`.toLowerCase().includes(search))
    && (!opts.sector || r.sector === opts.sector)
    && (opts.filter === 'all' || r.classification === opts.filter
      || (opts.filter === 'qualified' && r.qualified)
      || (opts.filter === 'reviewed' && !!r.candidate_review)
      || (opts.filter === 'analyst_risk' && r.risk_flags.some(f => f.kind === 'analyst'))))
    .sort((a, b) => {
      if (opts.sort === 'symbol') return (opts.ascending ? 1 : -1) * a.symbol.localeCompare(b.symbol);
      const av = a.metrics[opts.sort]?.value, bv = b.metrics[opts.sort]?.value;
      if (av == null && bv == null) return a.symbol.localeCompare(b.symbol);
      if (av == null) return 1;
      if (bv == null) return -1;
      return (opts.ascending ? 1 : -1) * (av - bv) || a.symbol.localeCompare(b.symbol);
    });
}

function csvCell(value: unknown) {
  let s = value == null ? '' : String(value);
  // Spreadsheet text must not execute a company name, symbol or provider note as a formula.
  if (typeof value !== 'number' && /^[\s]*[=+@-]/.test(s)) s = `'${s}`;
  return `"${s.replaceAll('"', '""')}"`;
}

export function valuationCsv(report: ValuationReport) {
  const metrics = ['price', 'fair_value', 'price_fair_value', 'discount_pct', 'upside_pct', 'trailing_pe', 'forward_pe', 'peg', 'rsi', 'eps_yoy_growth_pct'];
  const headers = ['report_id', 'version', 'generated_at', 'price_session', 'policy', 'symbol', 'name',
    'provider', 'classification', 'qualified', ...metrics.flatMap(k => [k, `${k}_state`, `${k}_reason`, `${k}_source`, `${k}_as_of`, `${k}_basis`]),
    'analyst_target_mean', 'analyst_target_note', 'annual_period', 'uncertainty', 'bear_value', 'bull_value',
    'base_growth_pct', 'base_discount_rate_pct', 'terminal_growth_pct', 'model_limitations',
    'source_url', 'fetched_at', 'blockers', 'review_version', 'review_date', 'review_status', 'review_decision',
    'review_sources', 'review_unresolved', 'thesis_invalidation', 'owner_cash_bear', 'owner_cash_base', 'owner_cash_bull', 'owner_cash_limits'];
  const body = report.valuation.rows.map(r => [report.id, report.version, report.generated_at, r.price_session,
    report.valuation.policy_version, r.symbol, r.name, r.provider || 'Yahoo', r.classification, r.qualified,
    ...metrics.flatMap(k => [r.metrics[k]?.value, r.metrics[k]?.state, r.metrics[k]?.reason,
      r.metrics[k]?.source, r.metrics[k]?.as_of, r.metrics[k]?.basis]),
    r.analyst.mean, r.analyst.note, r.valuation?.period_end, r.valuation?.uncertainty,
    r.valuation?.scenarios?.bear?.fair_value, r.valuation?.scenarios?.bull?.fair_value,
    r.valuation?.scenarios?.base?.growth_pct, r.valuation?.scenarios?.base?.discount_rate_pct,
    r.valuation?.scenarios?.base?.terminal_growth_pct, r.valuation?.limitations?.join('; '),
    r.source_url, r.fetched_at, r.blockers.join('; '), r.candidate_review?.version,
    r.candidate_review?.reviewed_at, r.candidate_review?.status, r.candidate_review?.decision,
    r.candidate_review?.sources.map(s => s.url).join('; '), r.candidate_review?.unresolved.join('; '),
    r.candidate_review?.invalidation.join('; '), r.candidate_review?.sensitivity?.scenarios.bear?.fair_value,
    r.candidate_review?.sensitivity?.scenarios.base?.fair_value, r.candidate_review?.sensitivity?.scenarios.bull?.fair_value,
    r.candidate_review?.sensitivity?.limitations.join('; ')]);
  return [headers, ...body].map(row => row.map(csvCell).join(',')).join('\r\n');
}
