import { useState, useMemo } from 'react';
import useSWR from 'swr';
import Link from 'next/link';
import { api, type SqueezeCandidate, type BearishPutsWatchCandidate, type SqueezeWatchItem } from '@/lib/api';

function Code({ children }: { children: React.ReactNode }) {
  return (
    <code style={{ background: '#0d1424', border: '1px solid #1e293b', borderRadius: '4px', padding: '1px 6px', fontSize: '12px', color: '#f59e0b', fontFamily: 'monospace' }}>
      {children}
    </code>
  );
}

function fmtShares(n: number | null): string {
  if (n == null) return '—';
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)}K`;
  return n.toString();
}

function fmtChg(v: number | null): string {
  if (v == null) return '—';
  return (v >= 0 ? '+' : '') + v.toFixed(2) + '%';
}

function fmtScore(v: number | null): string {
  if (v == null) return '—';
  return v.toFixed(1);
}

// ── T260-BEARISH-PUTS-WATCHLIST: watch/unwatch button, shared by both sections ─────────────

function WatchButton({
  symbol, watchType, priceAtAdd, metricAtAdd, watches, onChanged,
}: {
  symbol: string;
  watchType: 'short_squeeze' | 'bearish_puts';
  priceAtAdd: number | null;
  metricAtAdd: number | null;
  watches: SqueezeWatchItem[];
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const existing = watches.find(w => w.symbol === symbol && w.watch_type === watchType && !w.reverted);

  async function toggle() {
    setBusy(true);
    try {
      if (existing) {
        await api.removeSqueezeWatch(existing.id);
      } else {
        await api.addSqueezeWatch({ symbol, watch_type: watchType, price_at_add: priceAtAdd, metric_at_add: metricAtAdd });
      }
      onChanged();
    } catch {
      // best-effort — the button's own busy/disabled state already prevents a double-click
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      onClick={toggle}
      disabled={busy}
      title={existing ? 'Stop tracking — no more revert alerts for this symbol' : "Track this symbol and get an email the moment its short-side pressure fades"}
      style={{
        padding: '3px 9px', borderRadius: '5px', fontSize: '10.5px', fontWeight: 700, cursor: busy ? 'wait' : 'pointer',
        border: existing ? '1px solid rgba(56,189,248,0.4)' : '1px solid #1e293b',
        background: existing ? 'rgba(56,189,248,0.12)' : 'transparent',
        color: existing ? '#38bdf8' : '#64748b', whiteSpace: 'nowrap',
      }}
    >{existing ? '★ Watching' : '☆ Watch'}</button>
  );
}

function shortBg(pct: number): string {
  if (pct >= 40) return 'rgba(239,68,68,0.18)';
  if (pct >= 25) return 'rgba(249,115,22,0.15)';
  if (pct >= 15) return 'rgba(250,204,21,0.1)';
  return 'rgba(100,116,139,0.08)';
}

function shortColor(pct: number): string {
  if (pct >= 40) return '#ef4444';
  if (pct >= 25) return '#f97316';
  if (pct >= 15) return '#facc15';
  return '#94a3b8';
}

type SortKey = 'short_pct' | 'change_pct' | 'momentum' | 'k_score' | 'short_ratio';

// ── T260-BEARISH-PUTS-WATCHLIST: bearish puts watch section ──────────────────────────────────

function BearishPutsWatchSection({
  candidates, isLoading, watches, onWatchesChanged,
}: {
  candidates: BearishPutsWatchCandidate[];
  isLoading: boolean;
  watches: SqueezeWatchItem[];
  onWatchesChanged: () => void;
}) {
  const [showGuide, setShowGuide] = useState(false);
  if (isLoading) return null;

  return (
    <div style={{ marginTop: '28px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px', flexWrap: 'wrap', gap: '10px' }}>
        <div>
          <h2 style={{ fontSize: '16px', fontWeight: 800, color: '#e2e8f0', marginBottom: '2px' }}>Bearish Puts Watch</h2>
          <p style={{ fontSize: '11.5px', color: '#475569' }}>
            Puts-dominant options open interest, 3–5 days from expiry — the mirror of the classic squeeze above, for stocks under short-side pressure. See{' '}
            <Link href="/alerts-guide" style={{ color: '#38bdf8', textDecoration: 'none' }}>the Alerts Guide</Link> for the full mechanism.
          </p>
        </div>
        <button
          onClick={() => setShowGuide(s => !s)}
          style={{ padding: '5px 12px', borderRadius: '6px', border: '1px solid #1e293b', background: showGuide ? '#334155' : 'transparent', color: showGuide ? '#e2e8f0' : '#64748b', fontSize: '11.5px', cursor: 'pointer', whiteSpace: 'nowrap' }}
        >{showGuide ? '✕ Hide guide' : '📖 How to read this'}</button>
      </div>

      {showGuide && (
        <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: '12px', padding: '16px 18px', marginBottom: '16px', fontSize: '12px', lineHeight: 1.65, color: '#94a3b8' }}>
          <p style={{ marginBottom: 10 }}>
            A large block of <strong style={{ color: '#e2e8f0' }}>put</strong> options open interest concentrated
            near the current price, close to expiry, means market makers who sold those puts have a hedging
            obligation that intensifies as expiry nears — real pressure, but options positioning ALONE cannot
            tell you which direction it resolves.
          </p>
          <p style={{ marginBottom: 10 }}>
            <strong style={{ color: '#e2e8f0' }}>High conviction</strong> (green border below) means this stock&apos;s
            own AI Signal, RSI, and 50-day trend independently agree it&apos;s also bearish on its own separate
            merits — real corroborating evidence, not a guess from options data alone. Without that agreement,
            it&apos;s still worth watching, just not a stronger call than the data supports.
          </p>
          <p>
            <strong style={{ color: '#f87171' }}>This is never a guarantee the stock won&apos;t recover</strong> —
            even a high-conviction setup can reverse. Use ☆ Watch to track a symbol and get a one-shot email
            the moment the short-side pressure fades (price recovers, or the puts concentration drops back down).
          </p>
        </div>
      )}

      {candidates.length === 0 && (
        <div style={{ color: '#475569', fontSize: '13px', padding: '24px 0', textAlign: 'center', background: '#0f172a', border: '1px solid #1e293b', borderRadius: '12px' }}>
          No puts-dominant setups in the 3–5 day window right now.
        </div>
      )}

      {candidates.length > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '12px' }}>
          {candidates.map(c => (
            <div key={c.symbol} style={{
              background: '#0f172a', border: c.high_conviction ? '1px solid rgba(34,197,94,0.4)' : '1px solid #1e293b',
              borderRadius: '10px', padding: '14px 16px',
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '6px' }}>
                <Link href={`/stock/${c.symbol}`} style={{ color: '#818cf8', fontWeight: 700, fontSize: '14px', textDecoration: 'none' }}>{c.symbol}</Link>
                <span style={{ fontSize: '12.5px', fontWeight: 700, color: '#ef4444' }}>{c.concentration_pct.toFixed(0)}% puts</span>
              </div>
              {c.high_conviction && (
                <div style={{ fontSize: '10px', fontWeight: 800, color: '#22c55e', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                  ✓ High conviction — {c.agreeing_signals}/3 signals agree
                </div>
              )}
              <div style={{ fontSize: '11px', color: '#64748b', marginBottom: '4px' }}>
                {c.price != null ? `$${c.price.toFixed(2)}` : '—'} · {c.total_oi_near_money.toLocaleString()} contracts near the money
              </div>
              <div style={{ fontSize: '11px', color: '#64748b', marginBottom: '10px' }}>
                {/* AUD265-ZERO-DTE-OI-IS-STALE-BY-CONSTRUCTION: OI is exchange-published as of
                    the prior close — genuinely current for a 1-5 day-to-expiry row, but on the
                    expiry day itself the figure predates the whole trading session it's meant
                    to describe. Qualified only on that one row, matching the gamma-unwind
                    email's own equivalent fix. */}
                {c.days_to_expiry === 0 ? "expires TODAY (OI as of yesterday's close)" : `expires in ${c.days_to_expiry}d`} ({c.expiry})
                {c.ai_signal && <> · AI Signal: <span style={{ color: '#94a3b8' }}>{c.ai_signal}</span></>}
                {c.rsi != null && <> · RSI {c.rsi}</>}
              </div>
              <WatchButton
                symbol={c.symbol} watchType="bearish_puts"
                priceAtAdd={c.price} metricAtAdd={c.concentration_pct}
                watches={watches} onChanged={onWatchesChanged}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── T260-BEARISH-PUTS-WATCHLIST: "My Squeeze Watches" tracking panel ─────────────────────────

function MySqueezeWatchesPanel({ watches, onChanged }: { watches: SqueezeWatchItem[]; onChanged: () => void }) {
  if (watches.length === 0) return null;

  const active = watches.filter(w => !w.reverted);
  const reverted = watches.filter(w => w.reverted);

  async function remove(id: number) {
    try {
      await api.removeSqueezeWatch(id);
      onChanged();
    } catch { /* best-effort */ }
  }

  return (
    <div style={{ marginTop: '28px' }}>
      <h2 style={{ fontSize: '16px', fontWeight: 800, color: '#e2e8f0', marginBottom: '4px' }}>My Squeeze Watches</h2>
      <p style={{ fontSize: '11.5px', color: '#475569', marginBottom: '12px' }}>
        Symbols you&apos;re tracking from the sections above. You&apos;ll get an email the moment a
        watch&apos;s short-side pressure fades — checked every minute.
      </p>
      <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: '12px', overflow: 'hidden' }}>
        {active.map(w => (
          <div key={w.id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 16px', borderBottom: '1px solid rgba(30,41,59,0.5)', gap: '10px', flexWrap: 'wrap' }}>
            <div>
              <Link href={`/stock/${w.symbol}`} style={{ color: '#818cf8', fontWeight: 700, textDecoration: 'none', fontSize: '13px' }}>{w.symbol}</Link>
              <span style={{ fontSize: '11px', color: '#64748b', marginLeft: '8px' }}>
                {w.watch_type === 'short_squeeze' ? 'Short Squeeze' : 'Bearish Puts'} · added {new Date(w.added_at).toLocaleDateString()}
                {w.price_at_add != null && ` at $${w.price_at_add.toFixed(2)}`}
              </span>
            </div>
            <button onClick={() => remove(w.id)} style={{ padding: '3px 9px', borderRadius: '5px', fontSize: '10.5px', border: '1px solid #1e293b', background: 'transparent', color: '#64748b', cursor: 'pointer' }}>Stop tracking</button>
          </div>
        ))}
        {reverted.map(w => (
          <div key={w.id} style={{ padding: '10px 16px', borderBottom: '1px solid rgba(30,41,59,0.5)', background: 'rgba(34,197,94,0.03)' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '10px', flexWrap: 'wrap' }}>
              <div>
                <span style={{ color: '#4ade80', fontWeight: 700, fontSize: '13px' }}>↩ {w.symbol}</span>
                <span style={{ fontSize: '11px', color: '#64748b', marginLeft: '8px' }}>
                  {w.watch_type === 'short_squeeze' ? 'Short Squeeze' : 'Bearish Puts'} — reverted{w.reverted_at ? ` ${new Date(w.reverted_at).toLocaleDateString()}` : ''}
                </span>
              </div>
              <button onClick={() => remove(w.id)} style={{ padding: '3px 9px', borderRadius: '5px', fontSize: '10.5px', border: '1px solid #1e293b', background: 'transparent', color: '#64748b', cursor: 'pointer' }}>Remove</button>
            </div>
            {w.revert_reason && <div style={{ fontSize: '11px', color: '#4ade80', marginTop: '4px' }}>{w.revert_reason}</div>}
          </div>
        ))}
      </div>
    </div>
  );
}

export default function ShortSqueezePage() {
  const [minShortFloat, setMinShortFloat] = useState(10);
  const [market, setMarket] = useState<'All' | 'US' | 'HK'>('All');
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState<{ key: SortKey; dir: 'asc' | 'desc' }>({ key: 'short_pct', dir: 'desc' });
  const [showGuide, setShowGuide] = useState(false);

  const { data, error, isLoading, mutate } = useSWR<SqueezeCandidate[]>(
    `short-squeeze-${minShortFloat}`,
    () => api.shortSqueeze(minShortFloat),
    { revalidateOnFocus: false },
  );

  const { data: bearishPuts, isLoading: bearishLoading } = useSWR<BearishPutsWatchCandidate[]>(
    'bearish-puts-watch',
    () => api.bearishPutsWatch(),
    { revalidateOnFocus: false },
  );

  const { data: myWatches, mutate: mutateWatches } = useSWR<SqueezeWatchItem[]>(
    'squeeze-watches',
    () => api.listSqueezeWatches(),
    { revalidateOnFocus: false },
  );
  const watches = myWatches ?? [];

  const rows = useMemo(() => {
    let items = data ?? [];
    if (market !== 'All') items = items.filter(i => i.market === market);
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      items = items.filter(i => i.symbol.toLowerCase().includes(q) || i.name.toLowerCase().includes(q));
    }
    return [...items].sort((a, b) => {
      const getVal = (x: SqueezeCandidate): number => {
        if (sort.key === 'short_pct') return x.short_percent_of_float;
        if (sort.key === 'change_pct') return x.change_pct ?? -999;
        if (sort.key === 'momentum') return x.momentum_score ?? -999;
        if (sort.key === 'k_score') return x.k_score ?? -999;
        if (sort.key === 'short_ratio') return x.short_ratio ?? -999;
        return 0;
      };
      const diff = getVal(b) - getVal(a);
      return sort.dir === 'desc' ? diff : -diff;
    });
  }, [data, market, search, sort]);

  function toggleSort(key: SortKey) {
    setSort(s => s.key === key ? { key, dir: s.dir === 'desc' ? 'asc' : 'desc' } : { key, dir: 'desc' });
  }

  function SortTh({ label, col, right }: { label: string; col: SortKey; right?: boolean }) {
    const active = sort.key === col;
    return (
      <th onClick={() => toggleSort(col)} style={{ padding: '9px 14px', textAlign: right ? 'right' : 'left', color: active ? '#a78bfa' : '#475569', fontSize: '10px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', borderBottom: '1px solid #1e293b', background: '#080f1e', cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap' }}>
        {label} {active ? (sort.dir === 'desc' ? '↓' : '↑') : ''}
      </th>
    );
  }

  // Squeeze score: high short float + positive momentum = best candidates
  const topCandidates = rows.filter(r => r.momentum_score != null && r.momentum_score > 50 && r.short_percent_of_float >= 15);

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px', flexWrap: 'wrap', gap: '12px' }}>
        <div>
          <h1 style={{ fontSize: '22px', fontWeight: 800, color: '#e2e8f0', marginBottom: '4px' }}>Short Squeeze Scanner</h1>
          <p style={{ fontSize: '12px', color: '#475569' }}>
            High short interest stocks with upward momentum — classic squeeze setup. Want this proactively,
            without checking this page yourself? See <Link href="/alerts-guide" style={{ color: '#38bdf8', textDecoration: 'none' }}>short_squeeze_alert_check</Link> in the Alerts Guide — fires the moment a heavily-shorted stock starts moving fast, intraday.
          </p>
        </div>
        <div style={{ display: 'flex', gap: '8px' }}>
          <button
            onClick={() => setShowGuide(s => !s)}
            style={{ padding: '6px 14px', borderRadius: '6px', border: '1px solid #1e293b', background: showGuide ? '#334155' : 'transparent', color: showGuide ? '#e2e8f0' : '#64748b', fontSize: '12px', cursor: 'pointer' }}
          >{showGuide ? '✕ Hide guide' : '📖 How to read this page'}</button>
          <button
            onClick={() => mutate()}
            style={{ padding: '6px 14px', borderRadius: '6px', border: '1px solid #1e293b', background: 'transparent', color: '#64748b', fontSize: '12px', cursor: 'pointer' }}
          >↻ Refresh</button>
        </div>
      </div>

      {showGuide && (
        <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: '12px', padding: '18px 20px', marginBottom: '18px', fontSize: '12.5px', lineHeight: 1.7, color: '#94a3b8' }}>
          <div style={{ fontSize: '13px', fontWeight: 800, color: '#e2e8f0', marginBottom: 10 }}>
            What a short squeeze actually is
          </div>
          <p style={{ marginBottom: 14 }}>
            A short seller has borrowed and sold shares, betting the price falls, and must eventually buy
            them back to close the position (&quot;cover&quot;). If the price rises instead, every short
            seller is losing money and under pressure to buy back before the loss grows — that buying
            adds on top of whatever pushed the price up in the first place, which pushes it up further,
            which pressures the remaining shorts even harder. That self-reinforcing spiral is the squeeze.
            The columns below measure exactly how much fuel exists for that spiral, and whether it&apos;s
            already igniting.
          </p>

          <div style={{ fontSize: '13px', fontWeight: 800, color: '#e2e8f0', marginBottom: 10 }}>
            What each column means
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 14 }}>
            <div style={{ padding: '12px 14px', borderRadius: '10px', background: '#0d1424', border: '1px solid #1e293b' }}>
              <div style={{ fontSize: '12px', fontWeight: 800, color: '#facc15', marginBottom: 6 }}>Short % (of Float)</div>
              <div>
                How much of the stock&apos;s <em>tradeable</em> shares (its &quot;float&quot;, excluding
                insider/locked-up stock) are currently sold short. This is the <strong style={{ color: '#e2e8f0' }}>fuel</strong> —
                the more of the float is short, the more forced buying exists to potentially unwind.
                ≥15% is this app&apos;s own threshold for &quot;genuinely crowded.&quot; ≥40% is extreme
                and historically rare outside a handful of well-known squeeze events.
              </div>
            </div>
            <div style={{ padding: '12px 14px', borderRadius: '10px', background: '#0d1424', border: '1px solid #1e293b' }}>
              <div style={{ fontSize: '12px', fontWeight: 800, color: '#facc15', marginBottom: 6 }}>Days to Cover</div>
              <div>
                Shares short ÷ average daily trading volume — literally, how many normal trading days
                it would take for every short seller to buy back their position using only typical
                volume. This is the <strong style={{ color: '#e2e8f0' }}>fuse length</strong>: a high
                number means shorts can&apos;t exit quickly without moving the price a lot themselves,
                even in an orderly unwind. Under ~1-2 days, shorts can slip out quietly; 5+ days means
                real trapped exposure; 10+ is a genuinely thin market relative to the short position.
              </div>
            </div>
            <div style={{ padding: '12px 14px', borderRadius: '10px', background: '#0d1424', border: '1px solid #1e293b' }}>
              <div style={{ fontSize: '12px', fontWeight: 800, color: '#facc15', marginBottom: 6 }}>Shares Short (+ MoM change)</div>
              <div>
                The raw share count currently sold short, with the month-over-month change shown
                underneath. <span style={{ color: '#f87171' }}>↑ rising</span> short interest into a
                stock that&apos;s already moving up is a warning sign for shorts (more of them are
                adding to the position that&apos;s hurting them); <span style={{ color: '#4ade80' }}>↓
                falling</span> short interest can mean shorts are already voluntarily covering — often
                a sign a squeeze has already peaked and faded, not that one is building.
              </div>
            </div>
            <div style={{ padding: '12px 14px', borderRadius: '10px', background: '#0d1424', border: '1px solid #1e293b' }}>
              <div style={{ fontSize: '12px', fontWeight: 800, color: '#facc15', marginBottom: 6 }}>Change % / Momentum / K-Score</div>
              <div>
                These are the <strong style={{ color: '#e2e8f0' }}>spark</strong> — is the stock actually
                moving right now? A stock with huge short interest and a 10-day cover ratio sitting flat
                for weeks is <em>loaded</em>, not <em>active</em> — the squeeze mechanics can&apos;t start
                without price already rising. Momentum/K-Score come from this app&apos;s own ranking
                model (see the <Link href="/rankings" style={{ color: '#38bdf8', textDecoration: 'none' }}>Rankings page</Link> for
                what feeds into them), refreshed a few times a day rather than live intraday.
              </div>
            </div>
          </div>

          <div style={{ fontSize: '13px', fontWeight: 800, color: '#e2e8f0', marginBottom: 10 }}>
            How to use this to find what to focus on
          </div>
          <p style={{ marginBottom: 14 }}>
            Read the three signals together, not any single column alone: <strong style={{ color: '#e2e8f0' }}>Short %
            + Days to Cover</strong> tell you how much fuel/fuse exists (is this stock even structurally
            capable of a violent squeeze?), while <strong style={{ color: '#e2e8f0' }}>Change % and
            Momentum</strong> tell you whether it&apos;s already igniting <em>right now</em>. The{' '}
            <span style={{ color: '#f87171' }}>🔥 Prime Candidate</span> badge on this page marks rows
            where BOTH are true — short float ≥15% <em>and</em> real bullish momentum already underway —
            which is the closest thing to &quot;stop scanning and look at this one.&quot; A high-short-
            float stock with no 🔥 badge is worth bookmarking (it has the fuel), not acting on yet (no
            spark).
          </p>

          <div style={{ padding: '12px 14px', borderRadius: '10px', background: 'rgba(56,189,248,0.06)', border: '1px solid rgba(56,189,248,0.2)' }}>
            <div style={{ fontSize: '11px', fontWeight: 800, color: '#38bdf8', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 4 }}>
              Don&apos;t want to scan this page yourself?
            </div>
            The <Code>short_squeeze_alert_check</Code> job does exactly this combined read
            automatically, every minute, and pushes it to you the instant a heavily-shorted stock
            actually starts moving intraday — see{' '}
            <Link href="/alerts-guide" style={{ color: '#38bdf8', textDecoration: 'none' }}>the Alerts Guide</Link>{' '}
            for its exact thresholds and known limitations (it&apos;s deliberately long-only — no
            symmetric &quot;crowded longs unwinding&quot; version exists, since there&apos;s no reliable
            data source for that the way short interest exists for shorts).
          </div>
        </div>
      )}

      {/* Alert banner for top candidates */}
      {!isLoading && topCandidates.length > 0 && (
        <div style={{ background: 'rgba(239,68,68,0.06)', border: '1px solid rgba(239,68,68,0.2)', borderRadius: '10px', padding: '12px 16px', marginBottom: '18px', display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
          <span style={{ fontSize: '13px', fontWeight: 700, color: '#f87171' }}>🔥 {topCandidates.length} Prime Candidate{topCandidates.length > 1 ? 's' : ''}</span>
          <span style={{ fontSize: '12px', color: '#94a3b8' }}>High short interest + bullish momentum:</span>
          {topCandidates.slice(0, 5).map(c => (
            <Link key={c.symbol} href={`/stock/${c.symbol}`} style={{ fontSize: '11px', fontWeight: 700, color: '#f87171', background: 'rgba(239,68,68,0.1)', padding: '2px 8px', borderRadius: '4px', textDecoration: 'none' }}>
              {c.symbol} {c.short_percent_of_float.toFixed(0)}% short
            </Link>
          ))}
        </div>
      )}

      {/* Controls */}
      <div style={{ display: 'flex', gap: '10px', marginBottom: '18px', flexWrap: 'wrap', alignItems: 'center' }}>
        <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search symbol or name…"
          style={{ flex: '1 1 160px', padding: '7px 11px', borderRadius: '6px', border: '1px solid #1e293b', background: '#0f172a', color: '#e2e8f0', fontSize: '12px', outline: 'none' }} />
        <div style={{ display: 'flex', gap: '6px' }}>
          {(['All', 'US', 'HK'] as const).map(m => (
            <button key={m} onClick={() => setMarket(m)}
              style={{ padding: '5px 12px', borderRadius: '6px', fontSize: '12px', cursor: 'pointer', border: '1px solid #1e293b', background: market === m ? '#334155' : 'transparent', color: market === m ? '#e2e8f0' : '#64748b' }}
            >{m}</button>
          ))}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <label style={{ fontSize: '11px', color: '#64748b', whiteSpace: 'nowrap' }}>Min short %:</label>
          <select value={minShortFloat} onChange={e => setMinShortFloat(Number(e.target.value))}
            style={{ padding: '6px 10px', borderRadius: '6px', border: '1px solid #1e293b', background: '#0f172a', color: '#94a3b8', fontSize: '12px', cursor: 'pointer' }}>
            {[5, 10, 15, 20, 25, 30].map(v => <option key={v} value={v}>{v}%+</option>)}
          </select>
        </div>
      </div>

      {isLoading && <div style={{ color: '#475569', fontSize: '13px', padding: '40px 0', textAlign: 'center' }}>Scanning for squeeze candidates…</div>}
      {error && <div style={{ color: '#f87171', fontSize: '13px' }}>Failed to load scanner data.</div>}
      {!isLoading && !error && rows.length === 0 && (
        <div style={{ color: '#475569', fontSize: '13px', padding: '40px 0', textAlign: 'center' }}>
          No stocks found with {minShortFloat}%+ short interest.<br />
          <span style={{ fontSize: '11px' }}>Short interest data comes from cached fundamentals — visit stock pages to populate.</span>
        </div>
      )}

      {rows.length > 0 && (
        <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: '12px', overflow: 'hidden' }}>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
              <thead>
                <tr>
                  <th style={{ padding: '9px 14px', textAlign: 'left', color: '#475569', fontSize: '10px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', borderBottom: '1px solid #1e293b', background: '#080f1e', whiteSpace: 'nowrap' }}>Symbol</th>
                  <SortTh label="Short %" col="short_pct" right />
                  <SortTh label="Days to Cover" col="short_ratio" right />
                  <th style={{ padding: '9px 14px', textAlign: 'right', color: '#475569', fontSize: '10px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', borderBottom: '1px solid #1e293b', background: '#080f1e', whiteSpace: 'nowrap' }}>Shares Short</th>
                  <th style={{ padding: '9px 14px', textAlign: 'right', color: '#475569', fontSize: '10px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', borderBottom: '1px solid #1e293b', background: '#080f1e', whiteSpace: 'nowrap' }}>Price</th>
                  <SortTh label="Change" col="change_pct" right />
                  <SortTh label="Momentum" col="momentum" right />
                  <SortTh label="K-Score" col="k_score" right />
                  <th style={{ padding: '9px 14px', textAlign: 'right', color: '#475569', fontSize: '10px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', borderBottom: '1px solid #1e293b', background: '#080f1e', whiteSpace: 'nowrap' }}>Watch</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => {
                  const isPrime = r.momentum_score != null && r.momentum_score > 50 && r.short_percent_of_float >= 15;
                  return (
                    <tr key={i} style={{ borderBottom: '1px solid rgba(30,41,59,0.5)', background: isPrime ? 'rgba(239,68,68,0.03)' : 'transparent' }}>
                      <td style={{ padding: '10px 14px', whiteSpace: 'nowrap' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                          {isPrime && <span style={{ fontSize: '8px', color: '#f87171' }}>🔥</span>}
                          <Link href={`/stock/${r.symbol}`} style={{ color: '#818cf8', fontWeight: 700, textDecoration: 'none', fontSize: '13px' }}>
                            {r.symbol}
                          </Link>
                        </div>
                        <div style={{ fontSize: '10px', color: '#475569', maxWidth: '140px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.name}</div>
                        {r.sector && <div style={{ fontSize: '9px', color: '#334155' }}>{r.sector}</div>}
                      </td>
                      <td style={{ padding: '10px 14px', textAlign: 'right', whiteSpace: 'nowrap' }}>
                        <span style={{ fontWeight: 800, fontSize: '13px', color: shortColor(r.short_percent_of_float), background: shortBg(r.short_percent_of_float), padding: '2px 8px', borderRadius: '5px', fontVariantNumeric: 'tabular-nums' }}>
                          {r.short_percent_of_float.toFixed(1)}%
                        </span>
                      </td>
                      <td style={{ padding: '10px 14px', textAlign: 'right', color: '#94a3b8', fontVariantNumeric: 'tabular-nums' }}>
                        {r.short_ratio != null ? `${r.short_ratio.toFixed(1)}d` : '—'}
                      </td>
                      <td style={{ padding: '10px 14px', textAlign: 'right', color: '#64748b', fontVariantNumeric: 'tabular-nums' }}>
                        <div>{fmtShares(r.shares_short)}</div>
                        {r.shares_short != null && r.shares_short_prior_month != null && (() => {
                          const rising = r.shares_short > r.shares_short_prior_month!;
                          const pctChg = Math.abs((r.shares_short - r.shares_short_prior_month!) / r.shares_short_prior_month! * 100);
                          return (
                            <div style={{ fontSize: '9px', color: rising ? '#f87171' : '#4ade80', fontWeight: 700 }}>
                              {rising ? '↑' : '↓'} {pctChg.toFixed(0)}% MoM
                            </div>
                          );
                        })()}
                      </td>
                      <td style={{ padding: '10px 14px', textAlign: 'right', color: '#e2e8f0', fontVariantNumeric: 'tabular-nums' }}>
                        {r.price != null ? (r.price >= 100 ? r.price.toFixed(2) : r.price.toPrecision(4)) : '—'}
                      </td>
                      <td style={{ padding: '10px 14px', textAlign: 'right', fontWeight: 700, fontVariantNumeric: 'tabular-nums', color: (r.change_pct ?? 0) >= 0 ? '#4ade80' : '#f87171' }}>
                        {fmtChg(r.change_pct)}
                      </td>
                      <td style={{ padding: '10px 14px', textAlign: 'right' }}>
                        {r.momentum_score != null ? (
                          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: '5px' }}>
                            <div style={{ width: '32px', height: '4px', borderRadius: '2px', background: '#1e293b' }}>
                              <div style={{ width: `${Math.min(100, r.momentum_score)}%`, height: '100%', borderRadius: '2px', background: r.momentum_score > 60 ? '#22c55e' : r.momentum_score > 40 ? '#facc15' : '#ef4444' }} />
                            </div>
                            <span style={{ fontSize: '11px', color: '#94a3b8', fontVariantNumeric: 'tabular-nums' }}>{fmtScore(r.momentum_score)}</span>
                          </div>
                        ) : <span style={{ color: '#334155' }}>—</span>}
                      </td>
                      <td style={{ padding: '10px 14px', textAlign: 'right', color: '#94a3b8', fontVariantNumeric: 'tabular-nums' }}>
                        {r.k_score != null ? r.k_score.toFixed(1) : '—'}
                      </td>
                      <td style={{ padding: '10px 14px', textAlign: 'right' }}>
                        <WatchButton
                          symbol={r.symbol} watchType="short_squeeze"
                          priceAtAdd={r.price} metricAtAdd={r.short_percent_of_float}
                          watches={watches} onChanged={() => mutateWatches()}
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div style={{ padding: '10px 16px', fontSize: '11px', color: '#334155', borderTop: '1px solid #1e293b', display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: '6px' }}>
            <span>{rows.length} candidates · short interest from Yahoo Finance · momentum from K-Score model</span>
            <span style={{ color: '#1e293b' }}>🔥 = high short % + bullish momentum (prime squeeze candidate)</span>
          </div>
        </div>
      )}

      <BearishPutsWatchSection candidates={bearishPuts ?? []} isLoading={bearishLoading} watches={watches} onWatchesChanged={() => mutateWatches()} />
      <MySqueezeWatchesPanel watches={watches} onChanged={() => mutateWatches()} />
    </div>
  );
}
