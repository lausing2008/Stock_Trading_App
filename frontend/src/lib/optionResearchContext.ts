import type { Signal } from './api';

/** Existing outcome-evaluation windows; editable planning defaults, not optimal holding times. */
export const OPTION_HOLDING_DEFAULTS: Record<string, number> = { SHORT: 7, SWING: 14, LONG: 28, GROWTH: 14 };
export type OptionResearchContext = { symbol: string; horizon: string; signal?: Signal };
export type ContextEvidenceRow = { label: string; value: string; source: string; observation: string };

export function reviewOptionResearchContext(context: OptionResearchContext, now: number) {
  const signal = context.signal;
  const supported = Object.hasOwn(OPTION_HOLDING_DEFAULTS, context.horizon);
  const matches = typeof signal?.symbol === 'string' && signal.symbol.toUpperCase() === context.symbol.toUpperCase() && signal.horizon === context.horizon;
  // A timezone-less DB timestamp must not be interpreted in the browser's local timezone.
  const dated = typeof signal?.ts === 'string' && /(?:Z|[+-]\d{2}:\d{2})$/i.test(signal.ts);
  const recorded = dated ? Date.parse(signal!.ts!) : NaN;
  const ageHours = (now - recorded) / 3_600_000;
  const status = !supported ? 'unsupported_horizon' : !signal ? 'missing' : !matches ? 'mismatched'
    : !dated && typeof signal?.ts === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(signal.ts) ? 'timezone_unverified'
    : !Number.isFinite(recorded) ? 'undated' : ageHours < 0 ? 'future' : ageHours > 72 ? 'stale' : 'recorded';
  const statusLabel = { unsupported_horizon: 'Unsupported horizon', missing: 'Signal unavailable', mismatched: 'Wrong symbol or horizon', timezone_unverified: 'Timestamp timezone unverified', undated: 'Record date unavailable or invalid', future: 'Future-dated record', stale: 'Record older than 72 hours', recorded: 'Record within 72-hour limit' }[status];
  // Never borrow the selected symbol/horizon's label for another response's reasons.
  const reasons = matches && supported ? signal?.reasons ?? {} : {};
  const regime = ['bull', 'neutral', 'choppy', 'risk_off', 'bear'].includes(String(reasons.market_regime)) ? String(reasons.market_regime) : 'Unavailable';
  const above = reasons.trend_above_sma50;
  const order = reasons.sma50_above_sma200;
  const trend = typeof above === 'boolean' && typeof order === 'boolean'
    ? `Recorded above-SMA50 flag: ${above ? 'yes' : 'no'}; SMA50-above-SMA200 flag: ${order ? 'yes' : 'no'}`
    : 'Unavailable';
  const revision = reasons.eps_revision_direction;
  const outlook = revision === 1 ? 'Analyst recommendations improved' : revision === -1 ? 'Analyst recommendations weakened'
    : revision === 0 ? 'Analyst recommendations unchanged' : 'Unavailable';
  const rows: ContextEvidenceRow[] = [
    { label: 'AI signal', value: matches && supported && ['BUY', 'SELL', 'HOLD', 'WAIT'].includes(signal!.signal) ? signal!.signal : 'Unavailable', source: 'Selected-horizon signal record', observation: status === 'recorded' ? 'Record age checked; input observation dates unverified' : `Record status: ${statusLabel}` },
    { label: 'Market regime', value: regime, source: 'Recorded signal reasons', observation: 'Underlying observation date unavailable' },
    { label: 'Stock trend', value: trend, source: 'Recorded daily SMA50/200 diagnostics', observation: 'Underlying observation date unavailable; daily trend is not a horizon-specific forecast' },
    { label: 'Company outlook proxy', value: outlook, source: 'Recorded analyst recommendation revision', observation: 'Snapshot dates unavailable; this is not an EPS revision or company forecast' },
    { label: 'Option-horizon return forecast', value: 'Unavailable', source: 'No validated forecast distribution supplied', observation: 'Signal confidence and bullish probability do not establish option profitability' },
  ];
  const conflict = matches && supported && ((signal?.signal === 'BUY' && ['bear', 'risk_off'].includes(regime))
    || (signal?.signal === 'SELL' && regime === 'bull'));
  return { status, statusLabel, rows, recordedAt: matches && typeof signal?.ts === 'string' ? signal.ts : null, conflict,
    note: 'Market-aware preference unavailable: observation dates and a validated option-horizon forecast are missing. Recorded factors share one signal and are not independent votes.' };
}
