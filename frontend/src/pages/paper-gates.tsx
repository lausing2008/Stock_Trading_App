import React, { useEffect, useState, useCallback } from 'react';
import { useRouter } from 'next/router';
import Link from 'next/link';
import useSWR from 'swr';
import Head from 'next/head';
import { getSession } from '@/lib/auth';
import { api, type PaperPortfolioListItem, type RegimeStatus } from '@/lib/api';

// ── Gate pipeline data ────────────────────────────────────────────────────────

type GateRow = {
  gate: string;
  label: string;
  type: 'portfolio' | 'candidate' | 'score';
  trigger: string;
  clears: string;
  hk_diff?: string;
  severity: 'hard' | 'warn' | 'soft';
};

const GATE_PIPELINE: GateRow[] = [
  // ── Portfolio-level early returns ────────────────────────────────────────────
  {
    gate: 'market_hours', label: 'Market hours',
    type: 'portfolio', severity: 'hard',
    trigger: 'Outside 9:30–16:00 ET Mon–Fri (US). HK: 09:30–16:00 HKT.',
    clears: 'Automatically during next market session.',
    hk_diff: 'HK uses HKT window; checked separately.',
  },
  {
    gate: 'regime_bear', label: 'Bear market',
    type: 'portfolio', severity: 'hard',
    trigger: 'SPY < 200-EMA AND VIX > 30 — or SPY < 200-EMA with 20-day return < −8%.',
    clears: 'SPY recovers above 200-EMA OR VIX drops below 30.',
    hk_diff: 'HK has no VIX equivalent — uses HSI > 15% below SMA200 AND below SMA50. Downgraded to risk_off if breadth (% of tracked HK stocks above their own 200d SMA) is NOT also weak (≥40%) — a decline concentrated in a few heavyweights doesn\'t get the full bear treatment.',
  },
  {
    gate: 'regime_risk_off', label: 'Risk-off regime',
    type: 'portfolio', severity: 'hard',
    trigger: 'SPY < 50-EMA AND VIX > 25 (both conditions required). Default: fully blocked.',
    clears: 'SPY recovers above 50-EMA OR VIX falls below 25. Can be temporarily overridden per-portfolio for 4h or 1 day (Paper Portfolio → Config → Regime Gate Override) — self-expiring, reverts to 50%-size + score-5 behaviour while active.',
    hk_diff: 'HK: HSI 8–15% below SMA200 AND below SMA50 (no VIX — HK has no equivalent index). Same breadth confirmation as bear: downgraded to choppy if breadth ≥40% (not broadly weak) — decline looks concentrated rather than systemic. Same 4h/1day override available.',
  },
  {
    gate: 'regime_suspension', label: 'Sustained stress suspension',
    type: 'portfolio', severity: 'hard',
    trigger: 'Market has been risk_off or bear for 3+ consecutive calendar days (HK: 7 days).',
    clears: 'Regime improves to neutral or bull for 1 full day.',
    hk_diff: 'Threshold is 7 days (vs 3) — HSI stays stressed longer.',
  },
  {
    gate: 'entry_throttle', label: 'Regime entry throttle',
    type: 'portfolio', severity: 'warn',
    trigger: 'Choppy or risk_off regime AND 1 entry already made today.',
    clears: 'Next calendar day (midnight ET).',
    hk_diff: 'Same logic; applies when HSI regime is choppy.',
  },
  {
    gate: 'heat_brake', label: 'Heat brake (cascade stops)',
    type: 'portfolio', severity: 'hard',
    trigger: '3+ stop-outs in the last 48 hours across this portfolio.',
    clears: 'Oldest stop exit falls outside the 48h window.',
    hk_diff: 'Same thresholds (3 stops / 48h).',
  },
  {
    gate: 'index_trend', label: 'Index down >1.5% today',
    type: 'portfolio', severity: 'hard',
    trigger: 'SPY intraday return < −1.5% vs prior close (US). ^HSI < −1.5% for HK.',
    clears: 'Index recovers above −1.5% — checked on next scan cycle.',
    hk_diff: '^HSI is used for HK portfolios — US regime doesn\'t affect HK gate.',
  },
  {
    gate: 'drawdown', label: 'Portfolio drawdown',
    type: 'portfolio', severity: 'hard',
    trigger: 'Portfolio equity is >20% below its all-time high.',
    clears: 'Equity recovers above the drawdown threshold.',
    hk_diff: 'Same 20% limit.',
  },
  {
    gate: 'daily_loss', label: 'Daily loss limit',
    type: 'portfolio', severity: 'hard',
    trigger: 'Net realized P&L today < −4% of equity.',
    clears: 'Midnight ET (next calendar day).',
    hk_diff: 'Same 4% daily loss cap.',
  },
  {
    gate: 'weekly_loss', label: 'Weekly loss limit',
    type: 'portfolio', severity: 'hard',
    trigger: 'Net realized P&L this week < −8% of equity.',
    clears: 'Monday of next week (7-day rolling window).',
    hk_diff: 'Same 8% weekly loss cap.',
  },
  {
    gate: 'weekly_gain_lock', label: 'Weekly gain lock',
    type: 'portfolio', severity: 'warn',
    trigger: 'Net realized P&L this week > +1.5% of equity.',
    clears: 'Monday of next week — protects profits, not a failure state.',
    hk_diff: 'Same 1.5% gain lock.',
  },
  {
    gate: 'consecutive_losses', label: 'Consecutive-loss streak',
    type: 'portfolio', severity: 'hard',
    trigger: '3 consecutive losing trades without a winner (HK: 5 consecutive losses).',
    clears: 'Next trade closes positive.',
    hk_diff: 'HK threshold is 5 consecutive losses (vs 3) — HK has higher variance.',
  },
  {
    gate: 'daily_entry_cap', label: 'Daily entry cap',
    type: 'portfolio', severity: 'soft',
    trigger: '5 entries already made today in this portfolio.',
    clears: 'Midnight ET (next calendar day).',
    hk_diff: 'Same 5-entry/day cap.',
  },
  {
    gate: 'market_cluster_cap', label: 'Market position cap',
    type: 'portfolio', severity: 'soft',
    trigger: '4 open positions in this market (US or HK). Correlated positions all stop out together.',
    clears: 'Any open position in this market closes.',
    hk_diff: 'HK cap is the same 4 but applies separately from US.',
  },
  {
    gate: 'open_exposure', label: 'Open exposure cap',
    type: 'portfolio', severity: 'soft',
    trigger: 'Deployed capital > 40% of equity. Prevents over-committing to open positions.',
    clears: 'Any open position closes, reducing deployed capital.',
    hk_diff: 'Same 40% cap; HK positions are smaller (7% max vs 10% US) so cap is hit later.',
  },
  // ── Per-candidate filters ────────────────────────────────────────────────────
  {
    gate: 'stop_cooldown', label: 'Post-stop cooldown',
    type: 'candidate', severity: 'hard',
    trigger: 'Symbol was stopped out in the last 5 days (120h). Stock still in downtrend.',
    clears: '120 hours after the stop exit.',
    hk_diff: 'Same 5-day cooldown.',
  },
  {
    gate: 'global_symbol_cap', label: 'Cross-portfolio symbol cap',
    type: 'candidate', severity: 'hard',
    trigger: 'Symbol already open in ANY portfolio (max 1 global open per symbol).',
    clears: 'Other portfolio position in this symbol closes.',
    hk_diff: 'Same rule; applies across both US and HK portfolios.',
  },
  {
    gate: 'kscore', label: 'K-Score minimum',
    type: 'candidate', severity: 'soft',
    trigger: 'K-Score < 48. Stock ranks poorly on institutional momentum composite.',
    clears: 'Next ranking refresh (5×/week) updates the K-Score above threshold.',
    hk_diff: 'Same 48 minimum K-Score for HK stocks.',
  },
  {
    gate: 'signal_age', label: 'Signal freshness',
    type: 'candidate', severity: 'hard',
    trigger: 'BUY signal is older than 3 days (72h). Momentum from 4+ days ago may have exhausted.',
    clears: 'Next signal refresh cycle generates a fresh BUY signal.',
    hk_diff: 'Same 72h limit.',
  },
  {
    gate: 'confluence', label: 'Short-horizon confluence',
    type: 'candidate', severity: 'hard',
    trigger: 'GROWTH/LONG/SWING BUY rejected if SHORT-horizon signal = SELL. Near-term momentum is bearish.',
    clears: 'SHORT signal flips to BUY or HOLD on next refresh.',
    hk_diff: 'Same check applied to HK candidates.',
  },
  {
    gate: 'price_drift', label: 'Price drift (chasing)',
    type: 'candidate', severity: 'hard',
    trigger: 'Live price > 4% above the close at signal generation time. Signal is already priced in.',
    clears: 'Price retraces to within 4% of the signal close — or new signal generated.',
    hk_diff: 'Same 4% gap limit.',
  },
  {
    gate: 'hk_flow', label: 'HK Stock Connect flow gate',
    type: 'candidate', severity: 'soft',
    trigger: 'HK only: Southbound flow is net negative over last 5 days (mainland selling).',
    clears: 'Flow turns positive on next hk_connect_flows refresh (weekdays 17:00 HKT).',
    hk_diff: 'HK-only gate — not applied to US portfolios.',
  },
  // ── Entry scoring ────────────────────────────────────────────────────────────
  {
    gate: 'min_confidence', label: 'Signal confidence floor',
    type: 'score', severity: 'hard',
    trigger: 'Confidence < 55.8% (90% of 62% minimum). Hard reject regardless of score.',
    clears: 'New signal generation with higher confidence.',
    hk_diff: 'HK floor: 58.5% (90% of 65%).',
  },
  {
    gate: 'min_rr', label: 'Risk/reward ratio',
    type: 'score', severity: 'hard',
    trigger: 'R:R < 2.0 (take-profit distance < 2× stop distance). Bad setup geometry.',
    clears: 'Live price must be in a zone where the game plan gives 2:1 R:R.',
    hk_diff: 'Same 2.0 minimum R:R.',
  },
  {
    gate: 'earnings', label: 'Earnings proximity',
    type: 'score', severity: 'hard',
    trigger: 'Earnings in ≤ 5 days. Binary event risk on position entry.',
    clears: 'Earnings date passes.',
    hk_diff: 'HK earnings dates tracked where available; gate applies when date is known.',
  },
  {
    gate: 'min_entry_score', label: 'Entry score threshold',
    type: 'score', severity: 'hard',
    trigger: 'Composite score (multi-factor) < 4 (DEFAULT), < 5 (SWING/risk_off), < 6 (HK).',
    clears: 'Score points accumulate from: confidence, ATR, volume, RS, sector, options flow, S/R context.',
    hk_diff: 'HK requires score ≥ 6 (vs 4 default / 5 SWING) — stricter after 0% HK win rate.',
  },
];

// ── Regime display helpers ────────────────────────────────────────────────────

const REGIME_COLORS: Record<string, string> = {
  bull:     '#22c55e',
  neutral:  '#94a3b8',
  choppy:   '#f59e0b',
  risk_off: '#f97316',
  bear:     '#ef4444',
};

const REGIME_LABEL: Record<string, string> = {
  bull:     'Bull',
  neutral:  'Neutral',
  choppy:   'Choppy',
  risk_off: 'Risk-Off',
  bear:     'Bear',
};

const REGIME_ENTRY_STATUS: Record<string, { us: string; hk: string }> = {
  bull:     { us: 'Full size (100%)', hk: 'Full size (100%)' },
  neutral:  { us: 'Full size (100%)', hk: 'Full size (100%)' },
  choppy:   { us: '75% size, max 1 entry/day, score ≥4', hk: '75% size, max 1/day, score ≥6' },
  risk_off: { us: 'BLOCKED (overridable — Config → Regime Gate Override)', hk: 'BLOCKED unless breadth confirms recovery (overridable — see above)' },
  bear:     { us: 'BLOCKED (all entries suspended)', hk: 'BLOCKED (all entries suspended)' },
};

const SEVERITY_STYLE: Record<string, { color: string; bg: string; border: string; label: string }> = {
  hard: { color: '#f87171', bg: 'rgba(239,68,68,0.08)', border: 'rgba(239,68,68,0.25)', label: 'Blocks all' },
  warn: { color: '#f59e0b', bg: 'rgba(245,158,11,0.08)', border: 'rgba(245,158,11,0.25)', label: 'Limits' },
  soft: { color: '#94a3b8', bg: 'rgba(148,163,184,0.06)', border: 'rgba(148,163,184,0.2)', label: 'Per-symbol' },
};

const TYPE_LABEL: Record<string, string> = {
  portfolio: 'Portfolio gate',
  candidate: 'Per-candidate filter',
  score: 'Entry qualifier',
};

// ── Styles ────────────────────────────────────────────────────────────────────

const PAGE: React.CSSProperties = {
  minHeight: '100vh', background: '#0a0f1e', color: '#e2e8f0',
  fontFamily: 'ui-monospace, monospace', padding: '24px 20px', maxWidth: 1100, margin: '0 auto',
};
const SECTION: React.CSSProperties = { marginBottom: 32 };
const H2: React.CSSProperties = { fontSize: 14, fontWeight: 700, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 14, paddingBottom: 6, borderBottom: '1px solid #1e293b' };
const CARD: React.CSSProperties = { background: '#111827', border: '1px solid #1e293b', borderRadius: 10, padding: '14px 18px', marginBottom: 10 };
const TH: React.CSSProperties = { textAlign: 'left', padding: '6px 10px', fontSize: 10, fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em', borderBottom: '1px solid #1e293b', whiteSpace: 'nowrap' };
const TD: React.CSSProperties = { padding: '9px 10px', fontSize: 11, color: '#cbd5e1', verticalAlign: 'top', borderBottom: '1px solid #0f172a' };

export default function PaperGatesPage() {
  const router = useRouter();
  const [authed, setAuthed] = useState(false);
  const [filterType, setFilterType] = useState<'all' | 'portfolio' | 'candidate' | 'score'>('all');
  const [usRegime, setUsRegime] = useState<RegimeStatus | null>(null);
  const [hkRegime, setHkRegime] = useState<RegimeStatus | null>(null);
  const [regimeError, setRegimeError] = useState(false);

  useEffect(() => {
    const s = getSession();
    if (!s) { router.replace('/login'); return; }
    setAuthed(true);
  }, [router]);

  const loadRegime = useCallback(() => {
    setRegimeError(false);
    let failed = 0;
    api.regime('US').then(d => setUsRegime(d)).catch(() => { failed++; if (failed === 2) setRegimeError(true); });
    api.regime('HK').then(d => setHkRegime(d)).catch(() => { failed++; if (failed === 2) setRegimeError(true); });
  }, []);

  useEffect(() => {
    if (authed) loadRegime();
  }, [authed, loadRegime]);

  const { data: portfolios } = useSWR<PaperPortfolioListItem[]>(
    authed ? 'paper-list' : null,
    () => api.paperList(),
    { refreshInterval: 60_000 },
  );

  const filteredGates = GATE_PIPELINE.filter(g => filterType === 'all' || g.type === filterType);

  // Determine "buy conditions met?" for each market
  const usBlocked = usRegime && (usRegime.state === 'risk_off' || usRegime.state === 'bear');
  const hkBlocked = hkRegime && (hkRegime.state === 'risk_off' || hkRegime.state === 'bear');

  const activeBlocks = portfolios?.filter(p => p.entry_gate_block && p.is_active) ?? [];

  return (
    <>
      <Head><title>Entry Gate Reference — StockAI</title></Head>
      <div style={PAGE}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 24, flexWrap: 'wrap' }}>
          <Link href="/paper-portfolio" style={{ fontSize: 12, color: '#64748b', textDecoration: 'none' }}>← Paper Portfolio</Link>
          <h1 style={{ fontSize: 20, fontWeight: 700, color: '#f1f5f9', margin: 0 }}>Entry Gate Reference</h1>
          <span style={{ fontSize: 11, color: '#475569' }}>All conditions the engine checks before buying</span>
        </div>

        {/* ── Live Status ───────────────────────────────────────────────────── */}
        <div style={SECTION}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14 }}>
            <div style={H2}>Live Status</div>
            <button onClick={loadRegime} style={{ padding: '3px 10px', borderRadius: 6, fontSize: 11, fontWeight: 600, cursor: 'pointer', background: 'transparent', color: '#64748b', border: '1px solid #334155', marginBottom: 14 }}>↻ Refresh</button>
            {regimeError && <span style={{ fontSize: 11, color: '#f87171', marginBottom: 14 }}>Regime unavailable — decision-engine may be restarting</span>}
          </div>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
            {(['US', 'HK'] as const).map(mkt => {
              const r = mkt === 'US' ? usRegime : hkRegime;
              const blocked = mkt === 'US' ? usBlocked : hkBlocked;
              const rc = r ? REGIME_COLORS[r.state] : '#475569';
              return (
                <div key={mkt} style={{ ...CARD, flex: '1 1 260px', borderColor: rc + '44' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
                    <span style={{ fontSize: 12, fontWeight: 700, color: mkt === 'HK' ? '#fb923c' : '#22d3ee' }}>{mkt}</span>
                    {r ? (
                      <span style={{ fontSize: 11, fontWeight: 700, color: rc, background: rc + '20', border: `1px solid ${rc}44`, borderRadius: 5, padding: '2px 8px' }}>
                        {REGIME_LABEL[r.state] ?? r.state}
                      </span>
                    ) : regimeError ? (
                      <span style={{ fontSize: 11, color: '#f87171' }}>Unavailable</span>
                    ) : (
                      <span style={{ fontSize: 11, color: '#475569' }}>Fetching…</span>
                    )}
                    {r && (
                      <span style={{ fontSize: 11, fontWeight: 700, color: blocked ? '#ef4444' : '#22c55e', marginLeft: 'auto' }}>
                        {blocked ? '⊘ BLOCKED' : '✓ Entries open'}
                      </span>
                    )}
                  </div>
                  {r && (
                    <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', fontSize: 11, color: '#94a3b8' }}>
                      {mkt === 'US' && r.vix != null && <span>VIX <strong style={{ color: r.vix > 25 ? '#ef4444' : r.vix > 20 ? '#f59e0b' : '#22c55e' }}>{r.vix.toFixed(1)}</strong></span>}
                      {mkt === 'US' && r.spy_price != null && r.spy_ema50 != null && (
                        <span>SPY vs 50EMA <strong style={{ color: r.spy_price > r.spy_ema50 ? '#22c55e' : '#ef4444' }}>{r.spy_price > r.spy_ema50 ? '▲ Above' : '▼ Below'}</strong></span>
                      )}
                      {mkt === 'HK' && r.hsi_price != null && r.hsi_ema50 != null && (
                        <span>HSI vs 50SMA <strong style={{ color: r.hsi_price > r.hsi_ema50 ? '#22c55e' : '#ef4444' }}>{r.hsi_price > r.hsi_ema50 ? '▲ Above' : '▼ Below'}</strong></span>
                      )}
                      {r && <span>Entry size <strong style={{ color: '#e2e8f0' }}>{REGIME_ENTRY_STATUS[r.state]?.[mkt === 'US' ? 'us' : 'hk'] ?? '—'}</strong></span>}
                    </div>
                  )}
                  {r?.notes && r.notes.length > 0 && (
                    <div style={{ marginTop: 8, fontSize: 10, color: '#64748b', lineHeight: 1.5 }}>
                      {r.notes.slice(0, 2).join(' · ')}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Active blocks per portfolio */}
          {activeBlocks.length > 0 && (
            <div style={{ ...CARD, borderColor: 'rgba(251,146,60,0.3)' }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: '#fb923c', marginBottom: 10 }}>
                ⊘ Active portfolio gate blocks
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {activeBlocks.map(p => (
                  <div key={p.id} style={{
                    padding: '6px 12px', borderRadius: 7, background: 'rgba(251,146,60,0.08)',
                    border: '1px solid rgba(251,146,60,0.25)', fontSize: 11,
                  }}>
                    <div style={{ fontWeight: 700, color: '#f1f5f9', marginBottom: 2 }}>{p.name}</div>
                    <div style={{ color: '#fb923c' }}>{p.entry_gate_block!.reason}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
          {activeBlocks.length === 0 && portfolios && portfolios.length > 0 && (
            <div style={{ fontSize: 11, color: '#22c55e', background: 'rgba(34,197,94,0.08)', border: '1px solid rgba(34,197,94,0.2)', borderRadius: 8, padding: '8px 14px', display: 'inline-block' }}>
              ✓ No active portfolio-level gate blocks — entries can proceed if candidates qualify
            </div>
          )}
        </div>

        {/* ── Regime → Entry Status table ───────────────────────────────────── */}
        <div style={SECTION}>
          <div style={H2}>Regime → Entry Status</div>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr>
                  {['Regime', 'Trigger conditions', 'US entry', 'HK entry', 'Position size'].map(h => (
                    <th key={h} style={TH}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {[
                  {
                    regime: 'bull', trigger: 'SPY > 20-EMA & 50-EMA; VIX < 18',
                    us: 'Open — full size', hk: 'Open — full size', size: '100%',
                  },
                  {
                    regime: 'neutral', trigger: 'SPY above EMAs but VIX 18–20 or mixed signals',
                    us: 'Open — full size', hk: 'Open — full size', size: '100%',
                  },
                  {
                    regime: 'choppy', trigger: 'SPY below 20-EMA OR VIX > 20',
                    us: 'Open — max 1/day, score ≥4', hk: 'Open — max 1/day, score ≥6', size: '75%',
                  },
                  {
                    regime: 'risk_off', trigger: 'US: SPY below 50-EMA AND VIX > 25. HK: HSI 8–15% below SMA200 + below SMA50 (no VIX for HK), unless breadth confirms recovery.',
                    us: '⊘ BLOCKED (overridable 4h/1day)', hk: '⊘ BLOCKED (overridable 4h/1day)', size: '0 (blocked)',
                  },
                  {
                    regime: 'bear', trigger: 'US: SPY below 200-EMA AND VIX > 30. HK: HSI >15% below SMA200 + below SMA50, unless breadth confirms recovery.',
                    us: '⊘ BLOCKED', hk: '⊘ BLOCKED', size: '0 (blocked)',
                  },
                ].map(row => {
                  const rc = REGIME_COLORS[row.regime];
                  const blocked = row.regime === 'risk_off' || row.regime === 'bear';
                  return (
                    <tr key={row.regime} style={{ background: (usRegime?.state === row.regime || hkRegime?.state === row.regime) ? rc + '18' : 'transparent' }}>
                      <td style={{ ...TD, fontWeight: 700 }}>
                        <span style={{ color: rc, background: rc + '20', border: `1px solid ${rc}44`, borderRadius: 5, padding: '2px 8px', fontSize: 11 }}>
                          {REGIME_LABEL[row.regime]}
                        </span>
                        {(usRegime?.state === row.regime || hkRegime?.state === row.regime) && (
                          <span style={{ marginLeft: 6, fontSize: 9, color: '#94a3b8' }}>← now</span>
                        )}
                      </td>
                      <td style={{ ...TD, color: '#94a3b8' }}>{row.trigger}</td>
                      <td style={{ ...TD, color: blocked ? '#ef4444' : '#22c55e', fontWeight: 600 }}>{row.us}</td>
                      <td style={{ ...TD, color: blocked ? '#ef4444' : '#22c55e', fontWeight: 600 }}>{row.hk}</td>
                      <td style={{ ...TD, color: blocked ? '#ef4444' : '#94a3b8' }}>{row.size}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        {/* ── When will it buy? ──────────────────────────────────────────────── */}
        <div style={SECTION}>
          <div style={H2}>When will it buy?</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 16 }}>
            {(['US', 'HK'] as const).map(mkt => (
              <div key={mkt} style={{ ...CARD, borderColor: mkt === 'HK' ? 'rgba(251,146,60,0.25)' : 'rgba(34,211,238,0.2)' }}>
                <div style={{ fontSize: 13, fontWeight: 700, color: mkt === 'HK' ? '#fb923c' : '#22d3ee', marginBottom: 12 }}>
                  {mkt} — Entry prerequisites checklist
                </div>
                {[
                  {
                    label: 'Regime',
                    cond: mkt === 'HK'
                      ? 'Regime = bull, neutral, or choppy (score ≥6) — risk_off/bear downgraded one tier if breadth doesn\'t confirm broad weakness (≥40% of tracked HK stocks above their 200d SMA), or bypassed via a temporary override'
                      : 'Regime = bull or neutral (or choppy with score ≥4), or risk_off bypassed via a temporary override',
                    hk: mkt === 'HK',
                  },
                  { label: 'Index today', cond: `${mkt === 'HK' ? '^HSI' : 'SPY'} not down >1.5% on the day` },
                  { label: 'Heat brake', cond: 'Fewer than 3 stop-outs in the last 48 hours' },
                  { label: 'No loss limit', cond: 'Daily loss <4% | Weekly loss <8% | Drawdown <20%' },
                  { label: 'No consec losses', cond: mkt === 'HK' ? '<5 consecutive losing trades' : '<3 consecutive losing trades' },
                  { label: 'Positions available', cond: `<4 open ${mkt} positions; <5 entries today; <40% equity deployed` },
                  { label: 'BUY signal', cond: `Fresh BUY signal (≤3 days old) with confidence ≥${mkt === 'HK' ? '65' : '62'}%` },
                  { label: 'K-Score', cond: 'Stock K-Score ≥48 (institutional momentum composite)' },
                  { label: 'No chasing', cond: 'Live price within 4% of signal-generation close' },
                  { label: 'Confluence', cond: 'SHORT horizon not = SELL (for SWING/GROWTH/LONG styles)' },
                  { label: 'R:R', cond: 'Take-profit / stop ratio ≥ 2.0 at current price' },
                  { label: 'Entry score', cond: mkt === 'HK' ? 'Composite entry score ≥6 (stricter; 0% win rate before T222)' : 'Composite entry score ≥4 (SWING: ≥5)' },
                  ...(mkt === 'HK' ? [{ label: 'Flow gate', cond: 'Stock Connect southbound flow not strongly negative (net sellers)' }] : []),
                  { label: 'No earnings', cond: 'No earnings event within 5 days' },
                  { label: 'Not on cooldown', cond: 'Stock not stopped out in the past 5 days (120h)' },
                  { label: 'Not global open', cond: 'Stock not already open in any other portfolio' },
                ].map((item, i) => (
                  <div key={i} style={{ display: 'flex', gap: 8, marginBottom: 6, alignItems: 'flex-start' }}>
                    <span style={{ color: '#22c55e', fontWeight: 700, fontSize: 12, flexShrink: 0, marginTop: 1 }}>✓</span>
                    <div>
                      <span style={{ fontSize: 10, fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.04em' }}>{item.label} </span>
                      <span style={{ fontSize: 11, color: '#94a3b8' }}>{item.cond}</span>
                    </div>
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>

        {/* ── Full gate pipeline table ───────────────────────────────────────── */}
        <div style={SECTION}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14 }}>
            <div style={H2}>Full Gate Pipeline</div>
            <div style={{ display: 'flex', gap: 6, marginBottom: 14 }}>
              {(['all', 'portfolio', 'candidate', 'score'] as const).map(t => (
                <button key={t} onClick={() => setFilterType(t)} style={{
                  padding: '3px 10px', borderRadius: 6, fontSize: 11, fontWeight: 600, cursor: 'pointer',
                  background: filterType === t ? '#3b82f6' : 'transparent',
                  color: filterType === t ? '#fff' : '#64748b',
                  border: `1px solid ${filterType === t ? '#3b82f6' : '#334155'}`,
                }}>
                  {t === 'all' ? 'All' : TYPE_LABEL[t]}
                </button>
              ))}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 12, marginBottom: 12, flexWrap: 'wrap' }}>
            {(['hard', 'warn', 'soft'] as const).map(s => {
              const sv = SEVERITY_STYLE[s];
              return (
                <span key={s} style={{ fontSize: 10, fontWeight: 700, color: sv.color, background: sv.bg, border: `1px solid ${sv.border}`, borderRadius: 5, padding: '2px 8px' }}>
                  {sv.label === 'Blocks all' ? '⊘ Blocks entire portfolio' : sv.label === 'Limits' ? '⊘ Limits entries' : '⊘ Skips this candidate'}
                </span>
              );
            })}
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
              <thead>
                <tr>
                  {['Gate', 'Stage', 'Trigger (entries blocked when…)', 'Clears when…', 'HK difference'].map(h => (
                    <th key={h} style={{ ...TH, fontSize: 9 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filteredGates.map((g, i) => {
                  const sv = SEVERITY_STYLE[g.severity];
                  return (
                    <tr key={g.gate} style={{ background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.01)' }}>
                      <td style={{ ...TD, fontWeight: 700, whiteSpace: 'nowrap' }}>
                        <span style={{ color: sv.color, background: sv.bg, border: `1px solid ${sv.border}`, borderRadius: 4, padding: '1px 6px', fontSize: 10 }}>
                          {g.label}
                        </span>
                      </td>
                      <td style={{ ...TD, whiteSpace: 'nowrap', color: '#64748b' }}>
                        {TYPE_LABEL[g.type]}
                      </td>
                      <td style={TD}>{g.trigger}</td>
                      <td style={{ ...TD, color: '#4ade80' }}>{g.clears}</td>
                      <td style={{ ...TD, color: '#94a3b8' }}>{g.hk_diff ?? '—'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        {/* ── Score breakdown ───────────────────────────────────────────────── */}
        <div style={SECTION}>
          <div style={H2}>Entry Score Breakdown (+1 per condition met)</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 10 }}>
            {[
              { label: 'Signal confidence ≥ threshold', detail: 'BUY confidence above min_confidence (62% default, 65% HK)' },
              { label: 'Breakout / support context', detail: 'S/R analysis shows price at support or breaking resistance' },
              { label: 'Volume above average', detail: 'Today\'s volume elevated vs 20-day average (early institutional interest)' },
              { label: 'Relative strength rising', detail: 'RS score improving vs S&P 500 — stock outperforming index' },
              { label: 'Sector ETF above SMA50', detail: 'Stock\'s sector ETF (XLK, XLE, etc.) in an uptrend' },
              { label: 'Bullish options flow', detail: 'Put/call ratio below 0.5 — more call buying (bullish sentiment)' },
              { label: 'No recent news shock', detail: 'No negative news sentiment in last 24 hours' },
              { label: 'ATR within normal range', detail: 'Stock\'s volatility not abnormally elevated (spike = instability)' },
            ].map((item, i) => (
              <div key={i} style={{ display: 'flex', gap: 10, padding: '8px 12px', background: '#111827', border: '1px solid #1e293b', borderRadius: 8 }}>
                <span style={{ color: '#22c55e', fontWeight: 700, fontSize: 14, flexShrink: 0 }}>+1</span>
                <div>
                  <div style={{ fontSize: 11, fontWeight: 700, color: '#e2e8f0', marginBottom: 2 }}>{item.label}</div>
                  <div style={{ fontSize: 10, color: '#64748b' }}>{item.detail}</div>
                </div>
              </div>
            ))}
          </div>
          <div style={{ marginTop: 12, padding: '10px 14px', background: 'rgba(59,130,246,0.08)', border: '1px solid rgba(59,130,246,0.2)', borderRadius: 8, fontSize: 11 }}>
            <span style={{ fontWeight: 700, color: '#60a5fa' }}>Minimum to enter: </span>
            <span style={{ color: '#94a3b8' }}>Score ≥ 4 (default) · ≥ 5 (SWING or risk_off regime) · ≥ 6 (HK portfolios or choppy+HK)</span>
          </div>
        </div>

        {/* ── Footer ───────────────────────────────────────────────────────────── */}
        <div style={{ borderTop: '1px solid #1e293b', paddingTop: 16, fontSize: 10, color: '#334155', display: 'flex', gap: 20, flexWrap: 'wrap' }}>
          <span>Gates checked in order top → bottom. First block stops evaluation for that scan cycle.</span>
          <span>Regime + live prices refreshed every 2h. Portfolio gates rechecked every scan cycle (~every 30 min during market hours).</span>
          <Link href="/regime" style={{ color: '#475569', textDecoration: 'none' }}>→ Regime details</Link>
          <Link href="/paper-portfolio" style={{ color: '#475569', textDecoration: 'none' }}>→ Paper portfolio</Link>
          <Link href="/paper-portfolio#config" style={{ color: '#475569', textDecoration: 'none' }}>→ Risk-off gate override</Link>
          <Link href="/signal-filters" style={{ color: '#475569', textDecoration: 'none' }}>→ Signal filter</Link>
        </div>
      </div>
    </>
  );
}
