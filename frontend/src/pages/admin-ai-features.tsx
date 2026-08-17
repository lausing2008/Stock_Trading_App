import { useState, useEffect, useCallback } from 'react';
import { useRouter } from 'next/router';
import { getSession } from '@/lib/auth';
import { api, type PaperPortfolioListItem, type PaperPortfolioConfig } from '@/lib/api';

function Toggle({ on, onChange, disabled }: { on: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <button
      onClick={() => !disabled && onChange(!on)}
      style={{
        width: '44px', height: '24px', borderRadius: '12px', border: 'none',
        cursor: disabled ? 'default' : 'pointer', position: 'relative',
        background: on ? '#4f46e5' : '#1e293b', transition: 'background 0.2s',
        opacity: disabled ? 0.5 : 1, flexShrink: 0,
      }}
    >
      <span style={{
        position: 'absolute', top: '4px', left: on ? '22px' : '4px',
        width: '16px', height: '16px', borderRadius: '50%',
        background: '#fff', transition: 'left 0.2s',
      }} />
    </button>
  );
}

const card: React.CSSProperties = {
  borderRadius: '12px', border: '1px solid rgba(99,102,241,0.2)',
  background: 'rgba(15,23,42,0.95)', overflow: 'hidden', marginBottom: '16px',
};
const cardBar = (gradient: string): React.CSSProperties => ({ height: '3px', background: gradient });
const cardHead: React.CSSProperties = {
  padding: '14px 20px', borderBottom: '1px solid #1e293b',
  fontSize: '13px', fontWeight: 700, color: '#e2e8f0',
};
const cardBody: React.CSSProperties = { padding: '14px 20px' };
const modelBadge = (model: 'Haiku' | 'Sonnet'): React.CSSProperties => ({
  fontSize: '10px', fontWeight: 700, padding: '2px 8px', borderRadius: '4px',
  marginLeft: '8px',
  color: model === 'Sonnet' ? '#f87171' : '#4ade80',
  background: model === 'Sonnet' ? 'rgba(239,68,68,0.1)' : 'rgba(74,222,128,0.1)',
  border: `1px solid ${model === 'Sonnet' ? 'rgba(239,68,68,0.3)' : 'rgba(74,222,128,0.3)'}`,
});
const sectionLabel: React.CSSProperties = {
  fontSize: '10px', fontWeight: 700, color: '#334155', letterSpacing: '0.06em',
  textTransform: 'uppercase', marginBottom: '10px', marginTop: '28px',
};

function ToggleRow({
  title, desc, model, cadence, on, onChange, disabled,
}: {
  title: string; desc: string; model: 'Haiku' | 'Sonnet'; cadence: string;
  on: boolean; onChange: (v: boolean) => void; disabled?: boolean;
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
      <div style={{ flex: 1, minWidth: 220 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: '#e2e8f0' }}>
          {title}
          <span style={modelBadge(model)}>{model}</span>
        </div>
        <div style={{ fontSize: 11, color: '#64748b', marginTop: 4, lineHeight: 1.5 }}>{desc}</div>
        <div style={{ fontSize: 10, color: '#475569', marginTop: 6 }}>{cadence}</div>
      </div>
      <Toggle on={on} onChange={onChange} disabled={disabled} />
      <span style={{ fontSize: 11, color: on ? '#4ade80' : '#475569', fontWeight: 600, width: 28 }}>
        {on ? 'On' : 'Off'}
      </span>
    </div>
  );
}

function InfoRow({
  title, desc, model, cadence, cache,
}: {
  title: string; desc: string; model: 'Haiku' | 'Sonnet'; cadence: string; cache: string;
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
      <div style={{ flex: 1, minWidth: 220 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: '#e2e8f0' }}>
          {title}
          <span style={modelBadge(model)}>{model}</span>
        </div>
        <div style={{ fontSize: 11, color: '#64748b', marginTop: 4, lineHeight: 1.5 }}>{desc}</div>
        <div style={{ fontSize: 10, color: '#475569', marginTop: 6 }}>{cadence} · cache: {cache}</div>
      </div>
      <span style={{
        fontSize: 10, fontWeight: 700, color: '#818cf8', flexShrink: 0,
        padding: '3px 9px', borderRadius: '5px', background: 'rgba(99,102,241,0.1)',
        border: '1px solid rgba(129,140,248,0.3)',
      }}>
        Always on
      </span>
    </div>
  );
}

export default function AdminAiFeaturesPage() {
  const router = useRouter();
  const [authed, setAuthed] = useState(false);

  useEffect(() => {
    const s = getSession();
    if (!s) { router.replace('/login'); return; }
    if (s.role !== 'admin') { router.replace('/'); return; }
    setAuthed(true);
  }, [router]);

  // ── Global flags: auto-research report generation, macro reaction, earnings impact ──
  const [autoResearchEnabled, setAutoResearchEnabled] = useState(false);
  const [macroLlmReactionEnabled, setMacroLlmReactionEnabled] = useState(true);
  const [earningsLlmImpactEnabled, setEarningsLlmImpactEnabled] = useState(false);
  const [themeForecastEnabled, setThemeForecastEnabled] = useState(false);
  const [tradeCoachEnabled, setTradeCoachEnabled] = useState(false);
  const [globalSaving, setGlobalSaving] = useState<string | null>(null);

  useEffect(() => {
    if (!authed) return;
    api.getFeatureFlags().then(f => {
      setAutoResearchEnabled(f.auto_research_enabled);
      setMacroLlmReactionEnabled(f.macro_llm_reaction_enabled);
      setEarningsLlmImpactEnabled(f.earnings_llm_impact_enabled);
      setThemeForecastEnabled(f.theme_forecast_email_enabled);
      setTradeCoachEnabled(f.trade_coach_email_enabled);
    }).catch(() => {});
  }, [authed]);

  async function handleToggleAutoResearch(val: boolean) {
    setGlobalSaving('auto_research');
    try {
      await api.pushConfig({ auto_research_enabled: val });
      setAutoResearchEnabled(val);
    } catch { /* ignore */ } finally {
      setGlobalSaving(null);
    }
  }

  async function handleToggleMacroLlmReaction(val: boolean) {
    setGlobalSaving('macro_llm_reaction');
    try {
      await api.pushConfig({ macro_llm_reaction_enabled: val });
      setMacroLlmReactionEnabled(val);
    } catch { /* ignore */ } finally {
      setGlobalSaving(null);
    }
  }

  async function handleToggleEarningsLlmImpact(val: boolean) {
    setGlobalSaving('earnings_llm_impact');
    try {
      await api.pushConfig({ earnings_llm_impact_enabled: val });
      setEarningsLlmImpactEnabled(val);
    } catch { /* ignore */ } finally {
      setGlobalSaving(null);
    }
  }

  async function handleToggleThemeForecast(val: boolean) {
    setGlobalSaving('theme_forecast');
    try {
      await api.pushConfig({ theme_forecast_email_enabled: val });
      setThemeForecastEnabled(val);
    } catch { /* ignore */ } finally {
      setGlobalSaving(null);
    }
  }

  async function handleToggleTradeCoach(val: boolean) {
    setGlobalSaving('trade_coach');
    try {
      await api.pushConfig({ trade_coach_email_enabled: val });
      setTradeCoachEnabled(val);
    } catch { /* ignore */ } finally {
      setGlobalSaving(null);
    }
  }

  // ── Per-portfolio flags: LLM scoring layer + risk check ────────────────────
  const [portfolios, setPortfolios] = useState<PaperPortfolioListItem[]>([]);
  const [configs, setConfigs] = useState<Record<number, PaperPortfolioConfig>>({});
  const [portfolioSaving, setPortfolioSaving] = useState<number | null>(null);
  const [loadingPortfolios, setLoadingPortfolios] = useState(true);

  const loadPortfolios = useCallback(async () => {
    if (!authed) return;
    setLoadingPortfolios(true);
    try {
      const list = await api.paperList();
      setPortfolios(list);
      const entries = await Promise.all(
        list.map(async p => {
          try {
            const summary = await api.paperSummary(p.id);
            return [p.id, summary.config] as const;
          } catch {
            return null;
          }
        }),
      );
      const map: Record<number, PaperPortfolioConfig> = {};
      for (const e of entries) {
        if (e) map[e[0]] = e[1];
      }
      setConfigs(map);
    } catch { /* ignore */ } finally {
      setLoadingPortfolios(false);
    }
  }, [authed]);

  useEffect(() => { loadPortfolios(); }, [loadPortfolios]);

  async function handleTogglePortfolioFlag(
    portfolioId: number, key: 'llm_scoring_enabled' | 'risk_check_enabled', val: boolean,
  ) {
    setPortfolioSaving(portfolioId);
    try {
      const res = await api.paperConfigure({ [key]: val }, portfolioId);
      setConfigs(prev => ({ ...prev, [portfolioId]: res.config }));
    } catch { /* ignore */ } finally {
      setPortfolioSaving(null);
    }
  }

  if (!authed) return null;

  return (
    <div style={{ maxWidth: '900px', margin: '0 auto', padding: '24px 0' }}>
      <div style={{ marginBottom: '20px' }}>
        <h1 style={{ fontSize: '22px', fontWeight: 800, color: '#e2e8f0', marginBottom: '4px' }}>
          AI Assistant Features
        </h1>
        <p style={{ fontSize: '12px', color: '#475569' }}>
          Every place this app calls Claude (Anthropic) — what it does, how it can help, and
          which ones you can turn on or off. Costs scale with the model used and how often a
          feature fires — Sonnet is the most expensive; Haiku calls are cheap but frequent.
        </p>
      </div>

      {/* ── Toggleable features ──────────────────────────────────────────── */}
      <div style={sectionLabel}>Toggleable — you control these</div>

      <div style={card}>
        <div style={cardBar('linear-gradient(90deg,#6366f1,#818cf8,#6366f1)')} />
        <div style={cardHead}>Global</div>
        <div style={cardBody}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
            <ToggleRow
              title="Auto Research Report Generation"
              desc="Automatically writes a full AI research report (fundamentals, technicals, DCF valuation, catalysts) for BUY-signal stocks, so a report is ready before you click into a stock. Off by default — this is the most expensive AI feature in the app. A 2026-07-28 usage audit found and fixed a duplicate-trigger bug in market-data's own scheduler-side sweep; a 2026-07-29 follow-up found a SECOND, completely independent trigger inside signal-engine (every symbol with a BUY signal on any horizon, every signal-refresh cycle, with no cap at all) that had never been gated by this toggle at all — confirmed live: 46 distinct symbols BUY-signaled in one 24h window, generating 68 real reports despite nobody clicking Generate Report. Both trigger paths are now gated by this one switch."
              model="Sonnet"
              cadence="Scheduler sweep: up to 5 symbols per market refresh cycle (~77×/day for US alone). Signal-engine trigger: one per distinct BUY-signaled symbol per signal-refresh cycle (uncapped) if left on."
              on={autoResearchEnabled}
              onChange={handleToggleAutoResearch}
              disabled={globalSaving === 'auto_research'}
            />
            <ToggleRow
              title="Macro Reaction Analysis"
              desc="Reads the actual released number for a real CPI/PPI/GDP/NFP print or FOMC statement and writes a market-impact reaction paragraph plus which sectors it helps/hurts, then emails it to you. On by default — this has been live and relied upon since it first shipped, unlike the newer features below."
              model="Haiku"
              cadence="Only armed during the ~90-min window a real release could land, or on confirmed FOMC dates — essentially free the other ~360 days/year"
              on={macroLlmReactionEnabled}
              onChange={handleToggleMacroLlmReaction}
              disabled={globalSaving === 'macro_llm_reaction'}
            />
            <ToggleRow
              title="Earnings Impact Analysis"
              desc="The earnings-side counterpart to Macro Reaction Analysis, added on request: once a watched stock's real EPS/revenue actuals land, writes an impact paragraph (what the beat/miss means, and any read-through risk to peers/sector) plus which sectors it helps/hurts, then emails it to you. Off by default — a brand-new feature, same default-off convention as Auto Research above."
              model="Haiku"
              cadence="Polled every 5 min for newly-landed earnings without an impact read yet — cheap no-op most cycles"
              on={earningsLlmImpactEnabled}
              onChange={handleToggleEarningsLlmImpact}
              disabled={globalSaving === 'earnings_llm_impact'}
            />
            <ToggleRow
              title="Weekly Theme Signals"
              desc="A weekly digest of hand-curated themes (AI/GPU semiconductors, semiconductor packaging & testing, passive components, gold, space, healthcare, AI infrastructure, clean energy) with their real, already-measured 5-day price return, average K-Score, and current BUY/SELL signal counts, plus an AI-written summary explaining those numbers. Deliberately NOT a forecast of what any theme will do next — the AI is only asked to explain already-measured data, never to predict. Off by default — a brand-new feature."
              model="Haiku"
              cadence="Once weekly, Sunday 17:30 ET — one Haiku call per theme"
              on={themeForecastEnabled}
              onChange={handleToggleThemeForecast}
              disabled={globalSaving === 'theme_forecast'}
            />
            <ToggleRow
              title="Weekly Trade Pattern Review"
              desc="A weekly digest aggregating this account's own closed paper trades over the last 90 days: win rate and average return by exit reason, how far below its own peak price winning trades typically exit (a real, measurable 'giving back gains' read), and average hold days vs. each style's own expected window — plus an AI-written summary explaining those numbers. Deliberately does NOT give advice or predict future performance — it only describes already-measured patterns in how this account has traded. Off by default — a brand-new feature."
              model="Haiku"
              cadence="Once weekly, Sunday 17:45 ET — one Haiku call, skipped entirely if fewer than 10 closed trades exist in the window"
              on={tradeCoachEnabled}
              onChange={handleToggleTradeCoach}
              disabled={globalSaving === 'trade_coach'}
            />
          </div>
        </div>
      </div>

      <div style={card}>
        <div style={cardBar('linear-gradient(90deg,#22c55e,#4ade80,#22c55e)')} />
        <div style={cardHead}>Per Paper-Trading Portfolio</div>
        <div style={cardBody}>
          {loadingPortfolios && (
            <div style={{ fontSize: 12, color: '#475569', padding: '8px 0' }}>Loading portfolios…</div>
          )}
          {!loadingPortfolios && portfolios.length === 0 && (
            <div style={{ fontSize: 12, color: '#475569', padding: '8px 0' }}>No paper portfolios found.</div>
          )}
          {portfolios.map((p, idx) => {
            const cfg = configs[p.id];
            const saving = portfolioSaving === p.id;
            return (
              <div
                key={p.id}
                style={{
                  paddingTop: idx > 0 ? 16 : 0, paddingBottom: 16,
                  borderTop: idx > 0 ? '1px solid #1e293b' : undefined,
                }}
              >
                <div style={{ fontSize: 12, fontWeight: 700, color: '#94a3b8', marginBottom: 10 }}>
                  {p.name} <span style={{ color: '#475569', fontWeight: 400 }}>({p.trading_style} · {p.market})</span>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                  <ToggleRow
                    title="LLM Scoring Layer"
                    desc="After the quantitative entry gates pass, asks Claude to review the signal, confidence, regime, and research recommendation, then adds or subtracts points from the entry score based on its BUY/HOLD/SKIP verdict — a final qualitative sanity check before a real paper trade fires."
                    model="Haiku"
                    cadence="Runs on every candidate that clears the hard-reject gates, up to once per 5-min scan cycle per symbol"
                    on={!!cfg?.llm_scoring_enabled}
                    onChange={val => handleTogglePortfolioFlag(p.id, 'llm_scoring_enabled', val)}
                    disabled={saving || !cfg}
                  />
                  <ToggleRow
                    title="What-Could-Go-Wrong Risk Check"
                    desc="Runs an adversarial prompt asking Claude to argue AGAINST a trade that has already cleared every gate — surfaces concrete risk flags (macro, sector, company, technical) for you to read before entering, rather than a confidence score. Shows nothing when it finds no real risks, since a forced-adversarial prompt will otherwise almost always invent something to say."
                    model="Haiku"
                    cadence="Same trigger as the scoring layer above, cached 6h per symbol+style+date"
                    on={!!cfg?.risk_check_enabled}
                    onChange={val => handleTogglePortfolioFlag(p.id, 'risk_check_enabled', val)}
                    disabled={saving || !cfg}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* ── Always-on / info-only ────────────────────────────────────────── */}
      <div style={sectionLabel}>Always on — cached or rate-limited by design</div>
      <p style={{ fontSize: 11, color: '#475569', marginTop: '-4px', marginBottom: 14 }}>
        These already have a real cache or a narrow trigger window built in, so they don't
        carry the same runaway-cost risk the toggleable features above do. Shown here for
        visibility, not as something to turn off.
      </p>

      <div style={card}>
        <div style={cardBar('linear-gradient(90deg,#475569,#64748b,#475569)')} />
        <div style={cardBody}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
            <InfoRow
              title="Stock Sentiment Summary"
              desc="Summarizes recent news headlines for a stock into a bullish/neutral/bearish read, shown on the stock detail page."
              model="Haiku"
              cadence="Once per stock-page view"
              cache="4h per symbol"
            />
            <InfoRow
              title="Market Pulse Themes"
              desc="Reads today's top market-wide headlines (S&P 500, Fed, general market) and extracts up to 3 recurring themes plus an overall sentiment score."
              model="Haiku"
              cadence="Once per Market Pulse card view"
              cache="30 min"
            />
            <InfoRow
              title="Real-Time News Classification"
              desc="Classifies incoming press-release / SEC-filing / Alpaca news headlines by sentiment, materiality, and category as they arrive, and flags material negative news to suppress BUY signals."
              model="Haiku"
              cadence="Polled every 1-2 min, 24/7"
              cache="dedup by article URL — each real headline is classified once"
            />
          </div>
        </div>
      </div>

      <div style={sectionLabel}>User-initiated — not something to gate</div>
      <div style={card}>
        <div style={cardBar('linear-gradient(90deg,#475569,#64748b,#475569)')} />
        <div style={cardBody}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
            <InfoRow
              title="Research Report Chat"
              desc="Lets you ask follow-up questions about a generated research report directly in the report view."
              model="Sonnet"
              cadence="One call per chat message you send"
              cache="none — every message is a fresh call, same as talking to any chat assistant"
            />
            <InfoRow
              title="AI Assistant Chat"
              desc="The general-purpose AI chat assistant available across the app."
              model="Sonnet"
              cadence="One call per chat message you send"
              cache="none"
            />
          </div>
        </div>
      </div>

      <p style={{ fontSize: 11, color: '#334155', marginTop: 20 }}>
        All AI features use either a shared server-side key (set on the Settings page, admin
        only) or your own personal key if you've added one there — the same key/model powers
        every feature above. A missing or invalid key just means that feature silently does
        nothing (fails open), never a broken page.
      </p>
    </div>
  );
}
