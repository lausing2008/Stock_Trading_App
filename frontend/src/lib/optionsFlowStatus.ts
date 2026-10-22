import type { OptionsFlow } from './api';

/** Why the Options Flow card has no result — shown instead of silently hiding the section
 *  (options review F1, 2026-10-21: a route error made the card disappear for every symbol). */
export function optionsFlowUnavailableText(flow: OptionsFlow & { last_known_chain_date?: string | null }): string {
  switch (flow.reason) {
    case 'fetch_error':
      return 'Could not be computed right now (server error). This is not the same as no options activity.';
    case 'options_feed_unavailable':
      return `The options data feed has no current chain for this symbol${flow.last_known_chain_date ? ` (last chain ${flow.last_known_chain_date})` : ''}.`;
    case 'chain_read_failed':
      return 'The option chain could not be read for any selected expiration. This is a data failure, not an absence of activity.';
    case 'no_volume':
      return 'No call or put volume in the selected expirations.';
    case 'no_options_listed':
      return 'No listed options for this symbol.';
    default:
      return `Unavailable${flow.reason ? ` (${flow.reason})` : ''}.`;
  }
}

/** Phase A (options review F2/F3): composition labels from EXACT volumes. The agreed cut points
 *  are today's: call-heavy when C/P >= 1.3 (about 56.5% calls), put-heavy when C/P <= 0.8 (about
 *  55.6% puts), mixed otherwise. Shares are displayed rounded; classification never uses them. */
export const CP_CALL_HEAVY = 1.3;
export const CP_PUT_HEAVY = 0.8;
/** The legacy label's own floor (100 contracts), applied SYMMETRICALLY: when either side has
 *  fewer, the ratio rests on very few contracts of that side. */
export const LOW_SIDE_VOLUME = 100;

export function lowSideNote(call?: number | null, put?: number | null): string | null {
  if (call == null || put == null || call + put <= 0) return null;
  const [side, n] = call <= put ? ['calls', call] : ['puts', put];
  return n < LOW_SIDE_VOLUME ? `only ${n.toLocaleString()} ${side} (fewer than ${LOW_SIDE_VOLUME} contracts on that side)` : null;
}

/** Sum of known values with how many were known: a partial sum is never shown as complete. */
export function partialSum(values: (number | null | undefined)[]): { sum: number; known: number; total: number } {
  const known = values.filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
  return { sum: known.reduce((a, b) => a + b, 0), known: known.length, total: values.length };
}

export type Composition = {
  label: 'Call-heavy volume' | 'Put-heavy volume' | 'Mixed volume' | 'Volume not measured';
  kind: 'call_heavy' | 'put_heavy' | 'mixed' | 'unmeasured';
  callShare: number | null;   // 0..1, exact
  putShare: number | null;
  ratioText: string;          // e.g. "C/P 1.85" or "C/P undefined (no puts)"
};

export function composition(call?: number | null, put?: number | null): Composition {
  const c = call ?? 0, p = put ?? 0, total = c + p;
  if (call == null || put == null || total <= 0) {
    return { label: 'Volume not measured', kind: 'unmeasured', callShare: null, putShare: null, ratioText: 'C/P unavailable' };
  }
  const callShare = c / total, putShare = p / total;
  if (p === 0) {
    return { label: 'Call-heavy volume', kind: 'call_heavy', callShare, putShare, ratioText: 'C/P undefined (no puts)' };
  }
  if (c === 0) {
    return { label: 'Put-heavy volume', kind: 'put_heavy', callShare, putShare, ratioText: 'C/P 0 (no calls)' };
  }
  const ratio = c / p;   // exact: a display-rounded 1.30 from 1.296 must NOT read as call-heavy
  const kind = ratio >= CP_CALL_HEAVY ? 'call_heavy' : ratio <= CP_PUT_HEAVY ? 'put_heavy' : 'mixed';
  const label = kind === 'call_heavy' ? 'Call-heavy volume' : kind === 'put_heavy' ? 'Put-heavy volume' : 'Mixed volume';
  return { label, kind, callShare, putShare, ratioText: `C/P ${ratio.toFixed(2)}` };
}

export const pct1 = (x: number | null) => (x == null ? '—' : `${(x * 100).toFixed(1)}%`);

/** The archive's own completeness evidence, in words (options review B1). */
export function coverageStatusText(status?: string | null): string {
  switch (status) {
    case 'complete': return 'complete (archive capture reached its last page)';
    case 'incomplete': return 'incomplete — the archive capture did not finish';
    case 'partial': return 'partial — some expirations could not be read';
    case 'unverified_legacy': case 'unverified_legacy_cap': return 'unverified (captured before completeness was recorded)';
    case 'unverified': return 'unverified (not proven complete)';
    default: return status ?? 'unknown';
  }
}

/** Expiries a calculator may offer as a trade: the server's current list when present (it knows
 *  the exchange close and holidays); otherwise the older client-side date filter. */
export function tradeableExpiries(chain: { expiries?: string[]; current_expiries?: string[] } | undefined,
  daysToExpiry: (e: string) => number | null): string[] {
  if (chain?.current_expiries) return chain.current_expiries;
  return (chain?.expiries ?? []).filter(e => (daysToExpiry(e) ?? -1) >= 0);
}

/** Why a calculator has nothing to offer: the chain's own reason, or — when the chain loaded
 *  but every expiry of its session is past its last trade — say that rather than nothing. */
export function calculatorUnavailableReason(chain: { available?: boolean; reason?: string; current_expiries?: string[] } | undefined,
  tradeable: string[]): string | undefined {
  if (!chain) return undefined;
  if (!chain.available) return chain.reason ?? 'unavailable';
  if (chain.current_expiries && tradeable.length === 0) return 'every expiration in the archived session has stopped trading';
  return undefined;
}

/** True when the served expiry is not a current trade candidate. */
export function servedExpiryIsPast(chain: { expiry?: string; expiry_is_current?: boolean } | undefined,
  daysToExpiry: (e: string) => number | null): boolean {
  if (!chain?.expiry) return false;
  if (typeof chain.expiry_is_current === 'boolean') return !chain.expiry_is_current;
  return (daysToExpiry(chain.expiry) ?? -1) < 0;
}

/** "k of n expirations have expired since the session — historical evidence only", or null. */
export function expiredSinceLine(scope?: { historical_expiries: string[]; current_expiries: string[] } | null): string | null {
  if (!scope) return null;
  const expired = scope.historical_expiries.filter(e => !scope.current_expiries.includes(e));
  if (!expired.length) return null;
  return `${expired.length} of ${scope.historical_expiries.length} expirations (${expired.join(', ')}) have expired since that session — historical evidence only, not current trade candidates.`;
}

/** Source date and expiry coverage, shown on the card itself (not only in a tooltip). */
export function coverageLine(flow: { chain_as_of?: string | null; expiries_used?: string[] | null;
  expiries_attempted?: string[] | null; coverage_status?: string | null }): string {
  const used = flow.expiries_used?.length ?? 0;
  const tried = flow.expiries_attempted?.length ?? used;
  const status = coverageStatusText(flow.coverage_status);
  const names = flow.expiries_used?.length ? ` (${flow.expiries_used.join(', ')})` : '';
  return `Settled session ${flow.chain_as_of ? flow.chain_as_of.slice(0, 10) : 'unknown'} · ${used} of ${tried} expirations${names} · coverage ${status}`;
}

export const money = (v: number) => (v >= 1_000_000 ? `$${(v / 1_000_000).toFixed(1)}M` : v >= 1_000 ? `$${Math.round(v / 1_000)}K` : `$${Math.round(v)}`);

/** Paid premium only from the tape; never a chain mark. */
export function premiumText(c: { traded_premium?: number | null; premium?: number | null }): string {
  const v = c.traded_premium ?? c.premium;
  return v != null ? money(v) : 'unavailable';
}

/** Estimated turnover (volume x mid x 100) — an estimate, never called premium. */
export function turnoverText(c: { estimated_turnover?: number | null; turnover_basis?: string }): string {
  if (c.turnover_basis === 'withheld_nonstandard_deliverable') return 'withheld (adjusted contract; deliverable may not be 100 shares)';
  if (c.estimated_turnover == null) return 'unavailable';
  return `≈${money(c.estimated_turnover)}${c.turnover_basis === 'volume_x_last_price_x_100' ? ' (last price, no quote)' : ''}`;
}

/** Badge emphasis only (never displayed): points relative to the MEASURED maximum, so a
 *  score whose whale/GEX parts could not be measured is not held to an unreachable bar. */
export function pressureShare(p: { score: number; components: { max_possible?: number } }): number {
  const max = p.components.max_possible ?? 100;
  return max > 0 ? p.score / max : 0;
}

/** Pressure as a point total out of what could be measured — never a percentage (F5). */
export function pressureText(p: { score: number; components: { max_possible?: number } }): string {
  return p.components.max_possible != null ? `Pressure ${p.score.toFixed(0)} / ${p.components.max_possible.toFixed(0)} pts` : `Pressure ${p.score.toFixed(0)} pts`;
}

export function pressureTitle(p: { components: { cp_ratio_pts: number; whale_pts: number; volume_pts: number; uw_gex_proximity_pts?: number;
  measured?: string[]; unmeasured?: string[] } }): string {
  const c = p.components;
  return `Activity intensity points, not direction, confidence or a probability. Measured: ${(c.measured ?? []).join(', ') || 'unknown'}`
    + `${c.unmeasured?.length ? `; not measured: ${c.unmeasured.join(', ')}` : ''}. Compare only with scores measuring the same components. `
    + `cp_ratio ${c.cp_ratio_pts}/40 · volume ${c.volume_pts}/10${c.unmeasured?.includes('whale') ? '' : ` · whales ${c.whale_pts}/30`}`
    + `${c.uw_gex_proximity_pts != null ? ` · GEX proximity ${c.uw_gex_proximity_pts}/20` : ''}`;
}

/** "Shown 3 of 5 eligible calls and 1 of 1 eligible put" plus the rule. */
export function selectionLine(sel?: { rule: string; eligible: { call: number; put: number }; displayed: { call: number; put: number } } | null): string | null {
  if (!sel) return null;
  const part = (side: 'call' | 'put') => `${sel.displayed[side]} of ${sel.eligible[side]} eligible ${side}${sel.eligible[side] === 1 ? '' : 's'}`;
  return `Shown: ${part('call')} and ${part('put')}. Selection: ${sel.rule}`;
}

/** A request that failed is not "unavailable" data and not "no activity". */
export function requestFailedText(error: unknown): string {
  const msg = error instanceof Error ? error.message : String(error ?? '');
  if (/timed out/i.test(msg)) return 'Request timed out — the result is unknown, not empty. Try again.';
  if (/unauthori[sz]ed|session expired/i.test(msg)) return 'Request was not authorised — sign in again to load this section.';
  if (/^\d{3}\b/.test(msg)) return `Request failed (HTTP ${msg.slice(0, 3)}) — the result is unknown, not empty. Try again.`;
  return 'Request failed (network) — the result is unknown, not empty. Try again.';
}


// ── Options review B4/B5: off-exchange print time, side and baseline ──────────────────────

/** "14:32 ET · 12 min ago", or the quarantine reason — never a guessed time. */
export function printTimeText(p: { executed_at_utc?: string | null; time_status?: string; age_minutes?: number | null }): string {
  if (p.time_status && p.time_status !== 'ok') {
    return p.time_status === 'future' ? 'time in the future — quarantined (clock/feed error)'
      : p.time_status === 'ambiguous_timezone' ? 'no timezone — quarantined' : `time ${p.time_status}`;
  }
  if (!p.executed_at_utc) return 'time unknown';
  const t = new Date(p.executed_at_utc);
  const et = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(t);
  const age = p.age_minutes;
  const ageText = age == null ? '' : age < 60 ? ` · ${Math.round(age)} min ago` : age < 48 * 60 ? ` · ${(age / 60).toFixed(1)} h ago` : ` · ${Math.round(age / 1440)} d ago`;
  return `${et} ET${ageText}`;
}

export function sideEstimateText(side?: string | null): string {
  return side === 'buy' ? 'buy (est.)' : side === 'sell' ? 'sell (est.)' : 'undeterminable';
}

export function baselineLine(b?: { status: string; median: number | null; prints: number | null; window_days: number; min_prints: number; text?: string | null } | null): string | null {
  if (!b) return null;
  const base = b.text ?? b.status;
  const pop = `${b.prints ?? 'unknown'} captured prints in ${b.window_days} days (relative bar needs ${b.min_prints})`;
  return b.status === 'ok' && b.median != null ? `Baseline: median ${money(b.median)} — ${pop}; percentiles are relative to captured prints, not all market activity.`
    : `Baseline: ${base} — ${pop}.`;
}
