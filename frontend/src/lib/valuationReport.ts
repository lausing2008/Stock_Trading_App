import type { QvEvaluation } from './api';

export type ValuationMetric = { value: number | null; state: string; reason: string | null; unit?: string | null };
export type ValuationScenario = { fair_value: number; growth_pct: number; discount_rate_pct: number;
  terminal_growth_pct: number; terminal_value_share_pct: number; enterprise_value: number; equity_value: number };
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
export type ValuationFilter = 'all' | 'undervalued' | 'overvalued' | 'qualified' | 'analyst_risk';

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
  const metrics = ['price', 'fair_value', 'price_fair_value', 'discount_pct', 'upside_pct', 'trailing_pe', 'forward_pe', 'peg', 'rsi'];
  const headers = ['report_id', 'version', 'generated_at', 'price_session', 'policy', 'symbol', 'name',
    'provider', 'classification', 'qualified', ...metrics.flatMap(k => [k, `${k}_state`, `${k}_reason`]),
    'analyst_target_mean', 'analyst_target_note', 'annual_period', 'uncertainty', 'bear_value', 'bull_value',
    'base_growth_pct', 'base_discount_rate_pct', 'terminal_growth_pct', 'model_limitations',
    'source_url', 'fetched_at', 'blockers'];
  const body = report.valuation.rows.map(r => [report.id, report.version, report.generated_at, r.price_session,
    report.valuation.policy_version, r.symbol, r.name, r.provider || 'Yahoo', r.classification, r.qualified,
    ...metrics.flatMap(k => [r.metrics[k]?.value, r.metrics[k]?.state, r.metrics[k]?.reason]),
    r.analyst.mean, r.analyst.note, r.valuation?.period_end, r.valuation?.uncertainty,
    r.valuation?.scenarios?.bear?.fair_value, r.valuation?.scenarios?.bull?.fair_value,
    r.valuation?.scenarios?.base?.growth_pct, r.valuation?.scenarios?.base?.discount_rate_pct,
    r.valuation?.scenarios?.base?.terminal_growth_pct, r.valuation?.limitations?.join('; '),
    r.source_url, r.fetched_at, r.blockers.join('; ')]);
  return [headers, ...body].map(row => row.map(csvCell).join(',')).join('\r\n');
}
