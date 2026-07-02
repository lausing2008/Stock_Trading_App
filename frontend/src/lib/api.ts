const BASE = process.env.NEXT_PUBLIC_API_URL ?? '/api';

function authHeader(): Record<string, string> {
  if (typeof window === 'undefined') return {};
  const token = localStorage.getItem('stockai_jwt')?.trim() || null;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request<T>(path: string, init?: RequestInit, timeoutMs = 30_000): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  let r: Response;
  try {
    r = await fetch(`${BASE}${path}`, {
      ...init,
      signal: controller.signal,
      headers: { 'content-type': 'application/json', ...authHeader(), ...(init?.headers ?? {}) },
    });
  } catch (e: unknown) {
    if (e instanceof Error && e.name === 'AbortError') throw new Error('Request timed out');
    throw e;
  } finally {
    clearTimeout(timeout);
  }
  if (r.status === 401 && typeof window !== 'undefined' && !window.location.pathname.startsWith('/login')) {
    // Only log out if the JWT is actually expired. Transient 401s (e.g. during container
    // startup after a deployment) should not invalidate a locally-valid token.
    const raw = localStorage.getItem('stockai_jwt');
    let expired = true;
    if (raw) {
      try {
        const b64 = raw.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
        const payload = JSON.parse(atob(b64));
        expired = payload.exp < Date.now() / 1000;
      } catch { /* malformed token — treat as expired */ }
    }
    if (expired) {
      localStorage.removeItem('stockai_jwt');
      window.location.href = '/login';
      throw new Error('Session expired');
    }
    // Locally-valid token but server returned 401 — throw without logging out.
    // The caller's .catch() or SWR error state handles it gracefully.
    throw new Error('Unauthorized');
  }
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  if (r.status === 204 || r.headers.get('content-length') === '0') return undefined as T;
  return (await r.json()) as T;
}

export const api = {
  listStocks: (market?: string) => request<Stock[]>(`/stocks${market ? `?market=${market}` : ''}`),
  latestPrices: () => request<LatestPrice[]>(`/stocks/latest_prices`),
  latestPricesFor: (symbols: string[]) => request<LatestPrice[]>(`/stocks/latest_prices?symbols=${symbols.join(',')}`),
  getStock: (symbol: string) => request<Stock>(`/stocks/${symbol}`),
  getPrices: (symbol: string, tf = '1d', limit = 400, start?: string) =>
    request<Price[]>(`/stocks/${symbol}/prices?timeframe=${tf}&limit=${limit}${start ? `&start=${start}` : ''}`),
  pricesTf: (symbol: string, tf: '15m' | '1h' | '4h' | '1d') =>
    request<Price[]>(`/stocks/${symbol}/prices_tf?tf=${tf}`),
  overview: (symbol: string) => request<Overview>(`/aggregate/overview/${symbol}`),
  refreshFundamentals: (symbol: string) => request<unknown>(`/stocks/${symbol}/fundamentals?refresh=true`),
  rankings: (market?: string) => {
    const params = new URLSearchParams({ limit: '500' });
    if (market) params.set('market', market);
    return request<{ rankings: RankingRow[] }>(`/rankings?${params}`);
  },
  sectorRotation: (market?: string) =>
    request<SectorRotationReport>(`/rankings/sector_rotation${market ? `?market=${market}` : ''}`),
  screen: (params: {
    market?: string; sector?: string; signal?: string;
    min_confidence?: number; min_score?: number; max_score?: number;
    min_momentum?: number; min_technical?: number; min_rs?: number; min_growth?: number;
    sort_by?: string; limit?: number;
  }) => {
    const p = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== '') p.set(k, String(v)); });
    return request<{ total: number; items: {
      symbol: string; name: string; sector: string | null; market: string;
      score: number; technical: number; momentum: number; value: number; growth: number;
      rs_score: number | null; signal: string | null; confidence: number | null; horizon: string | null;
    }[] }>(`/rankings/screen?${p}`);
  },
  // live=false reads DB-stored signal (matches signal filter); live=true recomputes fresh
  signal: (symbol: string, style?: string, live = false) => {
    const params = new URLSearchParams({ live: String(live) });
    if (style) params.set('style', style);
    return request<Signal>(`/signals/${symbol}?${params}`);
  },
  allSignals: (style?: string) => request<SignalSummary[]>(`/signals${style ? `?style=${style}` : ''}`),
  signalConsensus: (market?: string) => request<Record<string, Record<string, { signal: string; confidence: number; bullish_probability: number | null; ts: string | null }>>>(`/signals/consensus${market ? `?market=${market}` : ''}`),
  convictionAll: () => request<Record<string, { sent: boolean; passed: string[]; failed: string[]; signal: string; ts: string }>>('/stocks/conviction'),
  kellySize: (style: string, lookbackDays?: number) => request<{ kelly_f: number | null; quarter_kelly: number | null; recommended_risk_pct: number; win_rate: number | null; avg_win_pct: number | null; avg_loss_pct: number | null; reward_risk_ratio: number | null; trades_count: number; note?: string }>(`/paper-portfolio/kelly?style=${style}${lookbackDays ? `&lookback_days=${lookbackDays}` : ''}`),
  signalHistory: (symbol: string, style = 'SWING', days = 60) =>
    request<SignalHistoryPoint[]>(`/signals/${symbol}/history?style=${style}&days=${days}`),
  signalChanges: (symbols: string[], hours = 48) =>
    request<SignalChange[]>(`/signals/recent_changes?symbols=${symbols.join(',')}&hours=${hours}`),
  getPatterns: (symbol: string) =>
    request<{ symbol: string; patterns: PatternSignal[]; as_of: string }>(`/signals/${symbol}/patterns`),
  refreshSignal: (symbol: string) => request<Signal>(`/signals/${symbol}?live=true&persist=true`),
  refreshSignals: (market?: string) => request<{ status: string; count: number }>(`/signals/refresh${market ? `?market=${encodeURIComponent(market)}` : ''}`, { method: 'POST' }),
  predict: (symbol: string, model = 'xgboost') =>
    request<Prediction>(`/ml/predict`, { method: 'POST', body: JSON.stringify({ symbol, model }) }),
  trainModel: (symbol: string, model = 'xgboost') =>
    request<{ status: string }>(`/ml/train`, { method: 'POST', body: JSON.stringify({ symbol, model }) }),
  listModels: () => request<string[]>(`/ml/models`),
  getNews: (symbol: string, sources = 'yfinance,google') =>
    request<NewsItem[]>(`/stocks/${symbol}/news?sources=${encodeURIComponent(sources)}`),
  createStrategy: (body: unknown) => request<{ id: number }>(`/strategies`, { method: 'POST', body: JSON.stringify(body) }),
  listStrategies: () => request<{ id: number; name: string; description?: string }[]>(`/strategies`),
  getStrategy: (sid: number) => request<{ id: number; name: string; rule_dsl: { entry: object; exit?: object }; description?: string }>(`/strategies/${sid}`),
  deleteStrategy: (sid: number) => request<{ status: string; id: number }>(`/strategies/${sid}`, { method: 'DELETE' }),
  backtest: (body: unknown) => request<Backtest>(`/backtest`, { method: 'POST', body: JSON.stringify(body) }),
  listBacktests: () => request<BacktestRun[]>(`/backtests`),
  getBacktest: (id: number) => request<BacktestDetail>(`/backtests/${id}`),
  deleteBacktest: (id: number) => request<{ status: string; id: number }>(`/backtests/${id}`, { method: 'DELETE' }),
  optimizePortfolio: (body: unknown) => request<PortfolioWeights>(`/portfolio/optimize`, { method: 'POST', body: JSON.stringify(body) }),
  ingest: (symbols: string[], force = false) => request<{ status: string; symbols?: number }>(`/admin/ingest`, { method: 'POST', body: JSON.stringify({ symbols, force }) }),
  trainAll: () => request<{ status: string; count: number; symbols: string[] }>(`/ml/train_all`, { method: 'POST' }),
  addStock: (symbol: string) => request<{ status: string; symbol: string; name: string; sector?: string }>(`/admin/add_stock`, { method: 'POST', body: JSON.stringify({ symbol }) }),
  deleteStock: (symbol: string) => request<{ status: string; symbol: string }>(`/admin/stocks/${symbol}`, { method: 'DELETE' }),
  marketOverview: () => request<MarketIndex[]>(`/stocks/market_overview`),
  fearGreed: () => request<FearGreed>(`/stocks/fear_greed`),
  marketBreadth: () => request<MarketBreadth>(`/stocks/market_breadth`),
  listWatchlists: () => request<WatchlistMeta[]>(`/watchlists`),
  createWatchlist: (name: string, trading_style?: string | null) => request<WatchlistMeta>(`/watchlists`, { method: 'POST', body: JSON.stringify({ name, trading_style }) }),
  renameWatchlist: (id: number, name: string, trading_style?: string | null) => request<WatchlistMeta>(`/watchlists/${id}`, { method: 'PUT', body: JSON.stringify({ name, trading_style }) }),
  deleteWatchlist: (id: number) => request(`/watchlists/${id}`, { method: 'DELETE' }),
  listWatchlist: (listId?: number) => request<WatchlistItem[]>(`/watchlist${listId != null ? `?list_id=${listId}` : ''}`),
  addToWatchlist: (symbol: string, listId?: number) => request<WatchlistItem>(`/watchlist/${symbol}${listId != null ? `?list_id=${listId}` : ''}`, { method: 'POST' }),
  removeFromWatchlist: (symbol: string, listId?: number) => request(`/watchlist/${symbol}${listId != null ? `?list_id=${listId}` : ''}`, { method: 'DELETE' }),
  updateWatchlistNote: (symbol: string, note: string | null, listId?: number) => request(`/watchlist/${symbol}/note${listId != null ? `?list_id=${listId}` : ''}`, { method: 'PATCH', body: JSON.stringify({ note }) }),
  isWatched: async (symbol: string): Promise<boolean> => {
    const items = await request<WatchlistItem[]>(`/watchlist`);
    return items.some(i => i.symbol === symbol);
  },

  // Price alerts
  listAlerts: () => request<PriceAlert[]>(`/alerts`),
  createAlert: (body: { symbol: string; condition: string; threshold: number; email?: string; note?: string; recurring?: boolean; webhook_url?: string }) =>
    request<PriceAlert>(`/alerts`, { method: 'POST', body: JSON.stringify(body) }),
  deleteAlert: (id: number) => request(`/alerts/${id}`, { method: 'DELETE' }),
  alertHistory: () => request<{ signal_alerts: { id: number; symbol: string; horizon: string | null; last_signal: string | null; last_sent_at: string | null }[]; price_alerts: { id: number; symbol: string; condition: string; threshold: number; triggered_at: string | null; note: string | null }[] }>(`/alerts/history`),

  // Signal alerts
  listSignalAlerts: () => request<SignalAlertItem[]>(`/signal-alerts`),
  createSignalAlert: (symbol: string, email?: string, alertMode?: string, horizon?: string, requireConsensus?: boolean) =>
    request<SignalAlertItem>(`/signal-alerts`, { method: 'POST', body: JSON.stringify({
      symbol, email, alert_mode: alertMode ?? 'all',
      horizon: horizon ?? 'SWING', require_consensus: requireConsensus ?? false,
    }) }),
  updateSignalAlert: (id: number, updates: { alert_mode?: string; require_consensus?: boolean }) =>
    request<SignalAlertItem>(`/signal-alerts/${id}`, { method: 'PATCH', body: JSON.stringify(updates) }),
  deleteSignalAlert: (id: number) => request(`/signal-alerts/${id}`, { method: 'DELETE' }),

  // Trade board (Kanban)
  listBoard: () => request<TradePlan[]>(`/board`),
  createBoardPlan: (body: {
    symbol: string; stage?: string; game_plan?: object | null;
    entry_price?: number | null; stop_loss?: number | null; take_profit?: number | null;
    notes?: string | null; source?: string | null; trading_style?: string | null;
  }) => request<TradePlan>(`/board`, { method: 'POST', body: JSON.stringify(body) }),
  updateBoardPlan: (id: number, body: { stage?: string; notes?: string; entry_price?: number; stop_loss?: number; take_profit?: number; exit_price?: number; actual_entry_price?: number; shares?: number; trading_style?: string }) =>
    request<TradePlan>(`/board/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteBoardPlan: (id: number) => request(`/board/${id}`, { method: 'DELETE' }),

  // User profile
  getMe: () => request<AppUser>(`/auth/me`),
  updateProfile: (body: { email?: string }) =>
    request<AppUser>(`/auth/me`, { method: 'PUT', body: JSON.stringify(body) }),
  syncAlertEmail: () =>
    request<{ ok: boolean; email: string; price_alerts_updated: number; signal_alerts_updated: number }>(`/auth/sync-alert-email`, { method: 'POST' }),

  // User management (admin)
  listUsers: () => request<AppUser[]>(`/auth/users`),
  impersonate: (username: string) => request<{ token: string; username: string; role: string }>(`/auth/impersonate/${username}`, { method: 'POST' }),
  createUser: (username: string, password: string, role: string) =>
    request<AppUser>(`/auth/users`, { method: 'POST', body: JSON.stringify({ username, password, role }) }),
  deleteUser: (username: string) => request(`/auth/users/${username}`, { method: 'DELETE' }),
  adminResetPassword: (username: string, newPassword: string) =>
    request(`/auth/users/${username}/reset-password`, { method: 'PUT', body: JSON.stringify({ new_password: newPassword }) }),
  toggleUser: (username: string) => request<{ is_active: boolean }>(`/auth/users/${username}/toggle`, { method: 'PUT' }),
  pushConfig: (keys: {
    polygon_api_key?: string; alpha_vantage_api_key?: string; quiver_api_key?: string;
    claude_api_key?: string; deepseek_api_key?: string;
    claude_model?: string; deepseek_model?: string;
    broker_enabled?: boolean;
  }) => request<{ status: string }>(`/admin/config`, { method: 'POST', body: JSON.stringify(keys) }),
  getFeatureFlags: () => request<{ broker_enabled: boolean }>(`/admin/feature-flags/public`),

  getAdminSignalLog: (params?: {
    symbol?: string; signal_type?: string; horizon?: string;
    days_back?: number; page?: number; limit?: number; market?: string;
  }) => {
    const p = new URLSearchParams();
    if (params?.symbol) p.set('symbol', params.symbol);
    if (params?.signal_type) p.set('signal_type', params.signal_type);
    if (params?.horizon) p.set('horizon', params.horizon);
    if (params?.days_back != null) p.set('days_back', String(params.days_back));
    if (params?.page != null) p.set('page', String(params.page));
    if (params?.limit != null) p.set('limit', String(params.limit));
    if (params?.market) p.set('market', params.market);
    return request<AdminSignalLogResponse>(`/admin/signal-log?${p.toString()}`);
  },

  // Broad stock scan (arbitrary tickers via yfinance)
  quickScan: (symbols: string[], priceMin?: number, priceMax?: number) =>
    request<QuickScanResult[]>(`/stocks/quick_scan`, {
      method: 'POST',
      body: JSON.stringify({ symbols, price_min: priceMin ?? null, price_max: priceMax ?? null }),
    }),

  // Congressional trading
  congressTrades: (days = 90, politician?: string) => {
    const params = new URLSearchParams({ days: String(days) });
    if (politician) params.set('politician', politician);
    return request<CongressTradeRecord[]>(`/congress/trades?${params}`);
  },

  // Signal accuracy tracker
  signalAccuracy: (lookbackDays = 90, symbol?: string, fromDate?: string, toDate?: string, page = 1, pageSize = 200, market?: string) => {
    const params = new URLSearchParams({ lookback_days: String(lookbackDays), page: String(page), page_size: String(pageSize) });
    if (symbol) params.set('symbol', symbol);
    if (fromDate) params.set('from_date', fromDate);
    if (toDate) params.set('to_date', toDate);
    if (market) params.set('market', market);
    return request<SignalAccuracyReport>(`/signals/accuracy?${params}`);
  },
  resetSignals: () => request<{ status: string; deleted: number; repersisting: number }>('/signals/reset', { method: 'POST' }),
  factorExposure: (lookbackDays = 90) =>
    request<FactorExposureReport>(`/signals/factor-exposure?lookback_days=${lookbackDays}`),
  mlWeightValidation: (lookbackDays = 180) =>
    request<MLWeightValidation>(`/signals/ml-weight-validation?lookback_days=${lookbackDays}`),
  calibrateMlWeight: (lookbackDays = 180) =>
    request<{ applied: boolean; optimal_weight: number | null; optimal_accuracy: number; signal_count: number; lookback_days: number; previous_cap: number | null; curve: { weight: number; accuracy: number | null; avg_return_pct: number | null }[] }>(
      `/signals/calibrate_ml_weight?lookback_days=${lookbackDays}`,
      { method: 'POST' }
    ),
  tradePerformance: (lookbackDays = 180, symbol?: string, horizon = 'SWING', opts?: { waitExits?: boolean; maxHoldDays?: number; minConfidence?: number; market?: string }) => {
    const params = new URLSearchParams({ lookback_days: String(lookbackDays), horizon });
    if (symbol) params.set('symbol', symbol);
    if (opts?.market) params.set('market', opts.market);
    if (opts?.waitExits) params.set('wait_exits', 'true');
    if (opts?.maxHoldDays != null) params.set('max_hold_days', String(opts.maxHoldDays));
    if (opts?.minConfidence != null && opts.minConfidence > 0) params.set('min_confidence', String(opts.minConfidence));
    return request<TradePerformanceReport>(`/signals/trade_performance?${params}`);
  },
  suppressedSignals: (style = 'SWING', market?: string) =>
    request<SuppressedSignalRow[]>(`/signals/suppressed?style=${style}${market ? `&market=${market}` : ''}`),
  rollingAccuracy: (window = 30, lookbackDays = 180) =>
    request<{ window: number; lookback_days: number; series: { date: string; accuracy: number; signal_count: number }[]; drift_warning: boolean; latest_accuracy: number | null }>(`/signals/rolling_accuracy?window=${window}&lookback_days=${lookbackDays}`),
  walkForward: (testDays = 30, holdDays = 5, lookbackDays = 365) =>
    request<WalkForwardReport>(`/signals/walkforward?test_days=${testDays}&hold_days=${holdDays}&lookback_days=${lookbackDays}`),
  dataFreshness: () =>
    request<{ last_bar_ts: string | null; hours_ago: number | null; status: string }>(`/stocks/data_freshness`),
  outcomesSummary: (horizon?: string, days = 90, market?: string) => {
    const params = new URLSearchParams({ days: String(days) });
    if (horizon) params.set('horizon', horizon);
    if (market) params.set('market', market);
    return request<OutcomesSummary>(`/signals/outcomes/summary?${params}`);
  },
  symbolOutcomes: (symbol: string, horizon?: string, days = 90) => {
    const params = new URLSearchParams({ symbol, days: String(days) });
    if (horizon) params.set('horizon', horizon);
    return request<OutcomesSummary>(`/signals/outcomes/summary?${params}`);
  },
  evaluateOutcomes: () =>
    request<{ evaluated: number; skipped_open: number; skipped_no_price: number; updated_windows: number }>(
      '/signals/outcomes/evaluate', { method: 'POST' }
    ),
  calibrateOutcomes: (days = 180, minSamples = 15) =>
    request<OutcomesCalibration>(`/signals/outcomes/calibrate?days=${days}&min_samples=${minSamples}`),
  alphaDecay: (horizon = 'SWING', lookbackDays = 365, regime?: string) => {
    const params = new URLSearchParams({ horizon, lookback_days: String(lookbackDays) });
    if (regime) params.set('regime', regime);
    return request<AlphaDecayReport>(`/signals/alpha_decay?${params}`);
  },
  informationCoefficient: (horizon = 'SWING', lookbackDays = 365) =>
    request<{
      horizon: string; lookback_days: number; monthly_ic: { month: string; ic: number; n: number }[];
      ic_mean: number | null; ic_std: number | null; ic_ir: number | null;
      total_periods: number; quality: string; message?: string;
    }>(`/signals/information_coefficient?horizon=${horizon}&lookback_days=${lookbackDays}`),
  factorAttribution: (horizon = 'SWING', lookbackDays = 365, minCount = 10) =>
    request<{
      horizon: string; lookback_days: number; total_winners: number; total_losers: number;
      factors: { factor: string; win_pct: number; los_pct: number; edge: number; win_count: number; los_count: number }[];
      message?: string;
    }>(`/signals/factor_attribution?horizon=${horizon}&lookback_days=${lookbackDays}&min_count=${minCount}`),
  runStyleAutoTuner: () =>
    request<{ status: string; styles_tuned: number }>('/signals/tune_style_profiles', { method: 'POST' }),
  runWatchdog: () =>
    request<{ status: string; actions: Record<string, string> }>('/signals/watchdog', { method: 'POST' }),
  mlTuneAll: (nTrials = 60) =>
    request<{ status: string; count: number; symbols: string[] }>(`/ml/tune_all?n_trials=${nTrials}`, { method: 'POST' }),
  signalTuneStatus: () =>
    request<{
      as_of: string; config_loaded_at: string | null;
      styles: Record<string, {
        defaults: { buy_threshold_bull: number; ml_weight_cap: number; adx_min: number | null; breadth_compression: number | null };
        redis_overrides: { watchdog_threshold: number | null; calibrated_threshold: number | null; ml_weight_cap: number | null; adx_min: number | null; breadth_compression: number | null };
        effective: { buy_threshold_bull: number; ml_weight_cap: number; adx_min: number | null; breadth_compression: number | null };
        performance: { win_rate_14d: number | null; n_outcomes_14d: number; signals_7d: number };
        watchdog: { status: string; tighten_count: number; current_threshold: number | null };
      }>;
    }>('/signals/tune_status'),
  filterAudit: (lookbackDays = 180, style = 'SWING', holdDays = 10) =>
    request<{
      lookback_days: number; style: string; hold_days: number;
      n_buy_signals_found: number; n_with_return_data: number; overall_win_rate_pct: number | null;
      by_filter_count: { filter_count: number; trade_count: number; win_rate_pct: number | null; avg_return_pct: number | null }[];
      by_filter_name: {
        filter: string; n_active: number; n_inactive: number;
        win_rate_active: number | null; win_rate_inactive: number | null;
        avg_return_active: number | null; avg_return_inactive: number | null;
        edge_pct: number; verdict: string;
      }[];
    }>(`/signals/filter_audit?lookback_days=${lookbackDays}&style=${style}&hold_days=${holdDays}`),
  stockAtr: (symbol: string, period = 14) =>
    request<{ symbol: string; atr: number; close: number; stop_loss_2atr: number; period: number }>(`/stocks/${symbol}/atr?period=${period}`),
  portfolioRisk: (symbols: string[], weights?: number[]) => {
    const params = new URLSearchParams({ symbols: symbols.join(',') });
    if (weights) params.set('weights', weights.join(','));
    return request<{
      symbols: string[];
      weights: number[];
      correlation: number[][];
      betas: Record<string, number>;
      portfolio_beta: number;
      sector_weights: Record<string, number>;
      var_95_pct: number;
      benchmark: string;
      warnings: string[];
    }>(`/portfolio-risk/risk?${params}`);
  },

  // Trade Journal
  listJournal: () => request<JournalTrade[]>('/journal'),
  createJournalTrade: (body: JournalTradeIn) => request<JournalTrade>('/journal', { method: 'POST', body: JSON.stringify(body) }),
  updateJournalTrade: (id: number, body: JournalTradeIn) => request<JournalTrade>(`/journal/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteJournalTrade: (id: number) => request(`/journal/${id}`, { method: 'DELETE' }),

  // Positions
  listPositions: () => request<UserPosition[]>('/positions'),
  addPosition: (body: { symbol: string; shares: number; price: number; currency?: string }) =>
    request<UserPosition>('/positions', { method: 'POST', body: JSON.stringify(body) }),
  buyMorePosition: (id: number, body: { shares: number; price: number }) =>
    request<UserPosition>(`/positions/${id}/buy`, { method: 'POST', body: JSON.stringify(body) }),
  sellPosition: (id: number, body: { shares: number; price: number }) =>
    request<UserPosition | undefined>(`/positions/${id}/sell`, { method: 'POST', body: JSON.stringify(body) }),
  removePosition: (id: number) => request(`/positions/${id}`, { method: 'DELETE' }),
  getCash: () => request<{ USD: number; HKD: number }>('/positions/cash'),
  updateCash: (body: { USD: number; HKD: number }) =>
    request<{ USD: number; HKD: number }>('/positions/cash', { method: 'PUT', body: JSON.stringify(body) }),

  // App Notifications
  listNotifications: () => request<AppNotification[]>('/app-notifications'),
  createNotification: (body: { alert_id: string; symbol: string; message: string; triggered_at: string; current_value?: number }) =>
    request<AppNotification>('/app-notifications', { method: 'POST', body: JSON.stringify(body) }),
  markAllNotificationsRead: () => request('/app-notifications/read-all', { method: 'PUT' }),
  clearNotifications: () => request('/app-notifications', { method: 'DELETE' }),

  // Sector heatmap / performance
  sectorPerformance: () => request<SectorGroup[]>('/stocks/sector_performance'),
  sectorRotationEtf: () => request<{
    spy_1m: number | null;
    sectors: { etf: string; sector: string; ret_1w: number | null; ret_1m: number | null; ret_3m: number | null; vs_spy_1m: number | null; status: string }[];
    ts: string;
    error?: string;
  }>('/stocks/sector_rotation'),

  // Earnings calendar
  earningsCalendar: (daysAhead = 45) => request<EarningsItem[]>(`/stocks/earnings_calendar?days_ahead=${daysAhead}`),
  eventsCalendar: (daysAhead = 90) => request<CalendarEvent[]>(`/stocks/events/calendar?days_ahead=${daysAhead}`),

  // Analyst ratings feed
  analystRatings: (days = 30) => request<AnalystRating[]>(`/stocks/analyst_ratings?days=${days}`),

  // Short squeeze scanner
  shortSqueeze: (minShortFloat = 10) => request<SqueezeCandidate[]>(`/stocks/short_squeeze?min_short_float=${minShortFloat}`),

  // Short interest dashboard
  shortInterest: () => request<ShortInterestRow[]>('/stocks/short-interest'),

  // Relative performance (multi-symbol normalized)
  relativePerformance: (symbols: string[], days = 90) =>
    request<Record<string, RelPerfPoint[]>>(`/stocks/relative_performance?symbols=${symbols.join(',')}&days=${days}`),

  // Dividends
  getOptionsFlow: (symbol: string) => request<OptionsFlow>(`/stocks/${symbol}/options-flow`),
  getDividends: (symbol: string) => request<DividendData>(`/stocks/${symbol}/dividends`),

  // Institutional holders
  getInstitutional: (symbol: string) => request<InstitutionalData>(`/stocks/${symbol}/institutional`),

  // Research Intelligence Engine
  generateResearch: (symbol: string, body: ResearchRequestBody) =>
    request<ResearchReport>(`/research/${symbol}`, { method: 'POST', body: JSON.stringify(body) }, 200_000),
  getResearch: (symbol: string) => request<ResearchReport>(`/research/${symbol}`),
  getResearchSummary: (symbol: string) => request<ResearchSummary>(`/research/${symbol}/summary`),
  getResearchBatch: (symbols: string[]) => request<Record<string, ResearchSummary>>(`/research/batch?symbols=${symbols.join(',')}`),
  triggerResearch: (symbol: string) => request<{ status: string; symbol: string }>(`/research/${encodeURIComponent(symbol)}/trigger`, { method: 'POST' }),
  clearResearch: (symbol: string) => request(`/research/${symbol}`, { method: 'DELETE' }),
  chatResearch: (symbol: string, messages: {role: string; content: string}[], api_key: string, model: string, provider: string) =>
    request<{role: string; content: string}>(`/research/${symbol}/chat`, { method: 'POST', body: JSON.stringify({ messages, api_key, model, provider }) }, 60_000),
  aiChat: (messages: {role: string; content: string}[], system: string, provider: string, api_key: string, model: string) =>
    request<{content: string; model: string; provider: string}>(`/ai/chat`, { method: 'POST', body: JSON.stringify({ provider, api_key, model, messages, system, max_tokens: 1024, temperature: 0.1 }) }, 30_000),

  // WF-2 Paper Portfolio
  paperList: () => request<PaperPortfolioListItem[]>('/paper-portfolio/list'),
  paperToggleActive: (portfolioId: number, active: boolean) =>
    request<{ ok: boolean; id: number; is_active: boolean }>(`/paper-portfolio/${portfolioId}/active`, { method: 'PATCH', body: JSON.stringify({ active }) }),
  paperCreate: (body: { name: string; trading_style: string; market?: string; initial_capital: number }) =>
    request<{ ok: boolean; portfolio_id: number; name: string }>('/paper-portfolio/create', { method: 'POST', body: JSON.stringify(body) }),
  paperCompare: (days = 180) => request<PaperCompareData[]>(`/paper-portfolio/compare?days=${days}`),
  paperSummary: (portfolioId?: number | null) => {
    const q = portfolioId ? `?portfolio_id=${portfolioId}` : '';
    return request<PaperPortfolioSummary>(`/paper-portfolio/summary${q}`);
  },
  paperPositions: (portfolioId?: number | null) => {
    const q = portfolioId ? `?portfolio_id=${portfolioId}` : '';
    return request<PaperPosition[]>(`/paper-portfolio/positions${q}`);
  },
  paperTrades: (params?: { page?: number; limit?: number; symbol?: string; exit_reason?: string; portfolioId?: number | null }) => {
    const p = new URLSearchParams();
    if (params?.page) p.set('page', String(params.page));
    if (params?.limit) p.set('limit', String(params.limit));
    if (params?.symbol) p.set('symbol', params.symbol);
    if (params?.exit_reason) p.set('exit_reason', params.exit_reason);
    if (params?.portfolioId) p.set('portfolio_id', String(params.portfolioId));
    return request<PaperTradesResponse>(`/paper-portfolio/trades?${p}`);
  },
  paperTradesCsvUrl: (portfolioId?: number | null) => {
    const q = portfolioId ? `?portfolio_id=${portfolioId}` : '';
    return `${BASE}/paper-portfolio/trades/csv${q}`;
  },
  paperEquityCurve: (days = 180, portfolioId?: number | null) => {
    const q = portfolioId ? `&portfolio_id=${portfolioId}` : '';
    return request<PaperEquityPoint[]>(`/paper-portfolio/equity-curve?days=${days}${q}`);
  },
  paperDecisions: (params?: { page?: number; limit?: number; symbol?: string; days_back?: number; portfolioId?: number | null }) => {
    const p = new URLSearchParams();
    if (params?.page) p.set('page', String(params.page));
    if (params?.limit) p.set('limit', String(params.limit));
    if (params?.symbol) p.set('symbol', params.symbol);
    if (params?.days_back) p.set('days_back', String(params.days_back));
    if (params?.portfolioId) p.set('portfolio_id', String(params.portfolioId));
    return request<PaperDecisionsResponse>(`/paper-portfolio/decisions?${p}`);
  },
  paperAttribution: (portfolioId?: number | null) => {
    const q = portfolioId ? `?portfolio_id=${portfolioId}` : '';
    return request<{
      total_trades: number; message?: string;
      by_score: { band: string; count: number; win_rate: number | null; avg_return: number | null; profit_factor: number | null }[];
      by_confidence: { band: string; count: number; win_rate: number | null; avg_return: number | null; profit_factor: number | null }[];
      by_regime: { band: string; count: number; win_rate: number | null; avg_return: number | null; profit_factor: number | null }[];
      by_rr: { band: string; count: number; win_rate: number | null; avg_return: number | null; profit_factor: number | null }[];
      best_profile: { score_band: string; conf_band: string; win_rate: number; count: number } | null;
    }>(`/paper-portfolio/attribution${q}`);
  },
  paperConfigure: (body: Partial<PaperPortfolioConfig>, portfolioId?: number | null) => {
    const q = portfolioId ? `?portfolio_id=${portfolioId}` : '';
    return request<{ ok: boolean; config: PaperPortfolioConfig }>(`/paper-portfolio/configure${q}`, { method: 'POST', body: JSON.stringify(body) });
  },
  paperReset: (portfolioId?: number | null) => {
    const q = portfolioId ? `?portfolio_id=${portfolioId}` : '';
    return request<{ ok: boolean; positions_closed: number; cash_reset_to: number }>(`/paper-portfolio/reset${q}`, { method: 'POST' });
  },
  paperSetCapital: (body: { initial_capital?: number; current_cash?: number }, portfolioId?: number | null) => {
    const q = portfolioId ? `?portfolio_id=${portfolioId}` : '';
    return request<{ ok: boolean; initial_capital: number; current_cash: number }>(`/paper-portfolio/capital${q}`, { method: 'POST', body: JSON.stringify(body) });
  },
  paperSetEngine: (state: 'running' | 'paused' | 'stopped', portfolioId?: number | null) => {
    const q = portfolioId ? `?portfolio_id=${portfolioId}` : '';
    return request<{ ok: boolean; state: string; config: PaperPortfolioConfig }>(`/paper-portfolio/engine${q}`, { method: 'POST', body: JSON.stringify({ state }) });
  },
  paperManualExit: (tradeId: number, portfolioId?: number | null) => {
    const q = portfolioId ? `?portfolio_id=${portfolioId}` : '';
    return request<{ symbol: string; exit_price: number; pnl: number; pnl_pct: number; cash_after: number }>(
      `/paper-portfolio/trades/${tradeId}/exit${q}`, { method: 'POST' }
    );
  },
  paperTradeParams: () => request<Record<string, PaperTradeParamResult>>('/paper-portfolio/trade-params'),
  paperTuneParams: (style: string, nTrials = 80) =>
    request<{ status: string; style: string; n_trials: number }>(
      `/paper-portfolio/tune-params?style=${style}&n_trials=${nTrials}`, { method: 'POST' }
    ),
  rlStatus: () => request<{
    status: 'trained' | 'ready' | 'not_trained';
    n_trades?: number; win_rate?: number; threshold?: number;
    feature_importance?: Record<string, number>; trained_at?: string;
  }>('/rl-agent/status'),
  rlTrain: () => request<{ status: string }>('/rl-agent/train', { method: 'POST' }),
  entryFactors: () => request<{
    status: 'calibrated' | 'not_calibrated';
    n_trades?: number; win_rate?: number; threshold?: number;
    w_rr?: number; w_confidence?: number; w_score?: number; w_kscore?: number;
    calibrated_at?: string;
  }>('/paper-portfolio/entry_factors'),
  calibrateEntry: () => request<{ status: string }>('/paper-portfolio/calibrate-entry', { method: 'POST' }),
  schedulerStatus: () => request<{ jobs: SchedulerJob[] }>('/admin/scheduler-status'),
  healthDeep: () => request<ServiceHealthReport>('/health/deep'),
  mlMetrics: (model = 'xgboost') => request<MlMetricsList>(`/ml/metrics?model=${model}`),
  mlFeatureImportance: (symbol: string, model = 'xgboost') =>
    request<FeatureImportanceResult>(`/ml/features/${symbol}?model=${model}`),

  // ── Event Intelligence ──────────────────────────────────────────────────
  eventsOverview: () => request<EventIntelOverview>('/events/overview'),
  eventsEconomic: (days = 14, market = 'US') => request<EconomicEventsResponse>(`/events/economic?days=${days}&market=${market}`),
  eventsEarningsCalendar: (days = 14) => request<EarningsEvent[]>(`/events/earnings/calendar?days=${days}`),
  eventsEarningsSymbol: (symbol: string) => request<EarningsEvent[]>(`/events/earnings?symbol=${symbol}`),
  eventsInsider: (symbol: string, days = 90) => request<InsiderResponse>(`/events/insider/${symbol}?days=${days}`),
  eventsInsiderLeaderboard: (days = 30) => request<InsiderLeaderItem[]>(`/events/insider/leaderboard?days=${days}`),
  eventsCongress: (symbol: string, days = 90) => request<CongressResponse>(`/events/congress/${symbol}?days=${days}`),
  eventsCongressLeaderboard: (days = 90) => request<CongressLeaderItem[]>(`/events/congress/leaderboard?days=${days}`),
  eventsCongressRecent: (days = 30) => request<CongressTrade[]>(`/events/congress/recent?days=${days}`),
  eventsInstitutional: (symbol: string) => request<InstitutionalResponse>(`/events/institutional/${symbol}`),
  eventsPolitical: (days = 30) => request<PoliticalEvent[]>(`/events/political?days=${days}`),
  catalystScore: (symbol: string) => request<CatalystScore>(`/catalyst/${symbol}`),
  catalystLeaderboard: (limit = 20) => request<CatalystLeaderItem[]>(`/catalyst/leaderboard?limit=${limit}`),
  riskLeaderboard: (limit = 20) => request<CatalystLeaderItem[]>(`/catalyst/risk-leaderboard?limit=${limit}`),
  compositeLeaderboard: (limit = 20) => request<CatalystLeaderItem[]>(`/catalyst/composite-leaderboard?limit=${limit}`),

  // ── Broker integration ──────────────────────────────────────────────────
  brokerList: () => request<BrokerConnection[]>('/broker/connections'),
  brokerCreate: (body: CreateBrokerConnectionPayload) =>
    request<BrokerConnection>('/broker/connections', { method: 'POST', body: JSON.stringify(body) }),
  brokerUpdate: (id: number, body: { name?: string; account_id?: string }) =>
    request<BrokerConnection>(`/broker/connections/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  brokerDelete: (id: number) =>
    request<void>(`/broker/connections/${id}`, { method: 'DELETE' }),
  brokerOAuthStart: (id: number) =>
    request<{ authorize_url: string; instructions: string }>(`/broker/connections/${id}/oauth/start`, { method: 'POST' }),
  brokerOAuthComplete: (id: number, verifier: string) =>
    request<{ status: string; account_id: string | null }>(`/broker/connections/${id}/oauth/complete`, {
      method: 'POST', body: JSON.stringify({ verifier }),
    }),
  brokerReconnect: (id: number) =>
    request<{ status: string }>(`/broker/connections/${id}/reconnect`, { method: 'POST' }),
  brokerAccount: (id: number) =>
    request<BrokerAccountInfo>(`/broker/connections/${id}/account`),
  brokerGetPortfolioBroker: (portfolioId: number) =>
    request<{ broker_connection_id: number | null; broker: BrokerConnection | null }>(`/broker/paper-portfolios/${portfolioId}/broker`),
  brokerAssignPortfolio: (portfolioId: number, brokerConnectionId: number | null) =>
    request<{ status: string }>(`/broker/paper-portfolios/${portfolioId}/broker`, {
      method: 'PUT', body: JSON.stringify({ broker_connection_id: brokerConnectionId }),
    }),

  // ── Decision Engine ────────────────────────────────────────────────────────
  decide: (symbol: string, style = 'SWING') =>
    request<DecisionResult>(`/decide/${symbol}/explain?style=${style}`),
  decideBatch: (symbols: string[], style = 'SWING', market = 'US') =>
    request<DecisionResult[]>('/decide/batch', {
      method: 'POST',
      body: JSON.stringify({ symbols, style, market, equity: 100_000, open_positions: 0, max_positions: 6 }),
    }),
  regime: (market: 'US' | 'HK' = 'US') =>
    request<RegimeStatus>(`/decide/regime?market=${market}`),
  deDivergences: (limit = 100) =>
    request<DeDivergenceResponse>(`/paper-portfolio/de-divergences?limit=${limit}`),

  // ── Signal Quality / Calibration ──────────────────────────────────────────
  outcomesCalibration: (days = 180) =>
    request<CalibrationData>(`/signals/outcomes/calibration?days=${days}`),

  // ── Quarterly Financials (T230) ────────────────────────────────────────────
  quarterlyFinancials: (symbol: string) => request<QuarterlyRow[]>(`/stocks/${symbol}/quarterly`),
};

export type SuppressedSignalConditions = {
  weekly_gate: boolean;
  weekly_misalignment: boolean;
  adx_choppy: boolean;
  high_vol_regime: boolean;
  low_breadth: boolean;
  earnings_caution: boolean;
  earnings_level: string | null;
  negative_news: boolean;
  news_level: string | null;
  rs_lagging: boolean;
  bearish_options: boolean;
  options_level: string | null;
  stale_data: boolean;
  insufficient_history: boolean;
  compression_cap: boolean;
};

export type SuppressedSignalRow = {
  symbol: string;
  name: string;
  signal: string;
  horizon: string;
  confidence: number;
  bullish_probability: number | null;
  ts: string | null;
  conditions: SuppressedSignalConditions;
  suppression_count: number;
  market_regime: string | null;
  weekly_rsi: number | null;
  weekly_trend: string | null;
  rsi: number | null;
  adx: number | null;
  breadth_pct: number | null;
  days_to_earnings: number | null;
  news_sentiment: number | null;
  rs_score: number | null;
  conviction: {
    sent: boolean;
    passed: string[];
    failed: string[];
    signal: string;
    ts: string;
    sent_at: string | null;
  } | null;
  days_active: Record<string, number>;
  pillar_trend: number | null;
  pillar_momentum: number | null;
  pillar_volume: number | null;
  pillar_structure: number | null;
  pillars_active: number | null;
  insider_score: number | null;
  congress_score: number | null;
  catalyst_score: number | null;
  catalyst_prob_adj: number | null;
};

export type Stock = {
  id: number;
  symbol: string;
  name: string;
  name_zh?: string | null;
  market: string;
  exchange: string;
  sector?: string;
  currency: string;
};

export type Price = {
  ts: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

/** Typed subset of signal reasons returned by signal-engine _bulk_persist. */
export type SignalReasons = Record<string, unknown> & {
  // event / catalyst flags
  eight_k_flag?: boolean;
  eight_k_date?: string | null;
  // T220 institutional intelligence suite
  insider_cluster?: boolean;
  insider_buy_usd?: number | null;
  congress_buy?: boolean;
  sector_momentum?: number | null;   // +1 rising, 0 neutral, -1 falling
  squeeze_score?: number | null;     // 0–100 composite
  piotroski_score?: number | null;   // 0–9
  macro_blackout?: string | null;    // event name if within 2h window
  eps_revision_direction?: number | null; // +1 rising, 0 flat, -1 falling
  // T220-E: 13F institutional ownership
  inst_change_pct?: number | null;        // QoQ % change in institutional holdings
  inst_ownership_increased?: boolean;     // true when inst holdings up >5% QoQ
};

export type Signal = {
  symbol: string;
  signal: 'BUY' | 'SELL' | 'HOLD' | 'WAIT';
  horizon: string;
  confidence: number;
  bullish_probability: number;
  reasons: SignalReasons;
};

export type SignalSummary = { symbol: string; signal: 'BUY' | 'SELL' | 'HOLD' | 'WAIT'; horizon: string; confidence: number; bullish_probability: number | null; ts: string | null; stability_days?: number | null };
export type SignalHistoryPoint = { ts: string | null; signal: string; confidence: number; bullish_probability: number | null };
export type SignalChange = { symbol: string; name: string; horizon: string; from_signal: string; to_signal: string; ts: string; confidence: number; bullish_probability: number | null; prev_ts: string };
export type PatternSignal = { name: string; label: string; description: string; bullish: boolean };
export type RankingRow = { symbol: string; name: string; name_zh?: string | null; score: number | null; market: string; fair_price?: number | null; sector?: string | null; index_membership?: string | null; technical?: number | null; momentum?: number | null; value?: number | null; growth?: number | null; volatility?: number | null; relative_strength?: number | null; vol_ratio?: number | null; trailing_pe?: number | null; forward_pe?: number | null; peg_ratio?: number | null; revenue_growth?: number | null; earnings_growth?: number | null; debt_to_equity?: number | null; price_to_book?: number | null; held_percent_institutions?: number | null; held_percent_insiders?: number | null; market_cap?: number | null; patterns?: string[] };
export type ShortInterestRow = { symbol: string; name: string; market: string; short_percent_of_float: number | null; short_ratio: number | null; market_cap: number | null };
export type SectorRsStock = { symbol: string; name: string; rs_score: number | null; kscore: number | null; past_rs: number | null };
export type SectorRotationEntry = { sector: string; etf: string; avg_rs: number; rs_change: number | null; stock_count: number; leading: number; lagging: number; leading_pct: number; top_stocks: SectorRsStock[]; bottom_stocks: SectorRsStock[] };
export type SectorRotationReport = { as_of: string; sectors: SectorRotationEntry[] };
export type Prediction = { symbol: string; bullish_probability: number; confidence: number; direction: string };
export type Backtest = {
  backtest_id: number | null;
  total_return: number;
  cagr: number;
  sharpe: number;
  sortino?: number | null;
  calmar?: number | null;
  max_drawdown: number;
  win_rate: number;
  profit_factor: number;
  n_trades: number;
  equity_curve: { ts: string; equity: number }[];
  trades: { entry_ts: string; entry: number; exit_ts?: string; exit?: number; ret?: number }[];
  benchmark_cagr?: number | null;
  benchmark_total_return?: number | null;
  alpha?: number | null;
  benchmark_equity_curve?: { ts: string; equity: number }[];
};
export type BacktestRun = {
  id: number;
  name: string;
  symbol: string;
  start: string;
  end: string;
  total_return: number;
  cagr: number;
  sharpe: number;
  sortino?: number | null;
  calmar?: number | null;
  max_drawdown: number;
  win_rate: number;
  profit_factor: number;
  n_trades: number;
  created_at: string;
};
export type BacktestDetail = BacktestRun & {
  rule_dsl: { entry: any; exit?: any };
  equity_curve: { ts: string; equity: number }[];
  trades: { entry_ts: string; entry: number; exit_ts?: string; exit?: number; ret?: number }[];
  benchmark_cagr?: number | null;
  benchmark_total_return?: number | null;
  alpha?: number | null;
  benchmark_equity_curve?: { ts: string; equity: number }[];
};
export type PortfolioWeights = {
  method: string;
  weights: Record<string, number>;
  cash: number;
  expected_return?: number | null;
  expected_vol?: number | null;
  sharpe_ratio?: number | null;
  max_drawdown?: number | null;
  diversification?: number | null;
  dropped_symbols?: string[];
};
export type LatestPrice = { symbol: string; price: number; prev_close: number | null; change_pct: number | null; currency: string; volume: number | null; avg_volume: number | null };
export type MarketIndex = { name: string; ticker: string; market: string; price: number | null; change_pct: number | null };
export type WatchlistItem = { symbol: string; name: string; name_zh?: string | null; market: string; exchange: string; sector?: string; currency: string; added_at: string; note?: string | null };
export type WatchlistMeta = { id: number; name: string; item_count: number; trading_style: string | null; created_at: string };
export type NewsItem = { title: string; url: string; source: string; published_at: number; sentiment: number; sentiment_label: 'bullish' | 'bearish' | 'neutral'; thumbnail?: string };
export type AppUser = { id: number; username: string; role: 'admin' | 'user'; is_active: boolean; email?: string | null; created_at: string };
export type PriceAlert = { id: number; symbol: string; condition: string; threshold: number; email: string; note: string | null; triggered: boolean; triggered_at: string | null; recurring: boolean; last_sent_at: string | null; webhook_url: string | null; created_at: string };
export type SignalAlertItem = { id: number; symbol: string; email: string | null; last_signal: string | null; last_sent_at: string | null; alert_mode: string; horizon: string; require_consensus: boolean; created_at: string };
export type TradePlan = { id: number; symbol: string; stage: 'watch' | 'planning' | 'active' | 'closed'; game_plan: Record<string, unknown> | null; entry_price: number | null; stop_loss: number | null; take_profit: number | null; notes: string | null; source: string | null; exit_price: number | null; actual_entry_price: number | null; shares: number | null; trading_style: string | null; closed_at: string | null; created_at: string; updated_at: string };
export type CongressTradeRecord = { Ticker: string; Date: string; Politician: string; Transaction: string; Min: number | null; Max: number | null; Party: string | null; State: string | null; Chamber: string | null; ReportDate: string | null };
export type SignalAccuracyRow = { symbol: string; name: string; signal: 'BUY' | 'SELL'; confidence: number; bullish_probability: number | null; signal_date: string; entry_price: number; exit_price: number; pct_change: number; correct: boolean; days_held: number };
export type SignalAccuracyReport = { lookback_days: number; total_signals: number; buy_count: number; sell_count: number; buy_accuracy: number | null; sell_accuracy: number | null; overall_accuracy: number | null; avg_buy_return_pct: number | null; avg_sell_return_pct: number | null; profit_factor: number | null; page: number; page_size: number; has_more: boolean; signals: SignalAccuracyRow[] };
export type TradePair = { symbol: string; name: string; status: 'closed' | 'open'; entry_date: string; exit_date: string; entry_price: number; exit_price: number; pct_return: number; hold_days: number; win: boolean; exit_signal: string; entry_confidence: number };
export type EquityPoint = { date: string; equity: number };
export type TradePerformanceReport = { lookback_days: number; closed_trades: number; open_trades: number; win_rate: number | null; avg_return_pct: number | null; avg_win_pct: number | null; avg_loss_pct: number | null; profit_factor: number | null; avg_hold_days: number | null; total_return: number | null; sharpe: number | null; max_drawdown: number | null; calmar: number | null; spy_return: number | null; equity_curve: EquityPoint[]; by_symbol: { symbol: string; trades: number; win_rate: number; avg_return: number; avg_hold_days: number }[]; trades: TradePair[] };
export type FactorRow = { key: string; label: string; baseline: number; scale: number; correct_avg: number | null; wrong_avg: number | null; correct_dev_pct: number | null; wrong_dev_pct: number | null; correct_count: number; wrong_count: number };
export type FactorExposureReport = { lookback_days: number; signal_count: number; factors: FactorRow[] };
export type MLWeightCurvePoint = { weight: number; accuracy: number | null; avg_return_pct: number | null };
export type WalkForwardWindow = { start: string; end: string; n_signals: number; n_correct: number; accuracy: number; avg_return_pct: number; equity: number };
export type OutcomesBand = { band: string; count: number; win_rate: number; avg_return_pct: number | null };
export type ResearchAlignmentBand = { count: number; win_rate: number | null; avg_return_pct: number | null };
export type OutcomesSummary = {
  total: number;
  days_lookback: number;
  message?: string;
  date_range?: { oldest: string | null; newest: string | null };
  overall?: { win_rate: number; avg_return_pct: number | null; median_return_pct: number | null };
  by_confidence_band?: OutcomesBand[];
  by_horizon?: Record<string, { count: number; win_rate: number; avg_return_pct: number | null }>;
  by_market?: Record<string, { count: number; win_rate: number; avg_return_pct: number | null }>;
  by_direction?: Record<string, { count: number; win_rate: number; avg_return_pct: number | null }>;
  by_market_regime?: Record<string, { count: number; win_rate: number; avg_return_pct: number | null }>;
  by_research_alignment?: Record<'aligned' | 'partial' | 'divergent' | 'no_research', ResearchAlignmentBand>;
  by_window?: Record<'5d' | '10d' | '20d', { count: number; win_rate: number; avg_return_pct: number | null } | null>;
  by_symbol?: { symbol: string; count: number; win_rate: number; avg_return_pct: number | null; wins: number; losses: number }[];
};
export type OutcomesCalibrationRow = {
  horizon: string;
  current_threshold: number;
  suggested_threshold: number | null;
  ev_lift_pct: number | null;
  n_total: number;
  note?: string;
  at_current_threshold?: { n: number; win_rate: number; avg_return_pct: number; expected_value_pct: number } | null;
  at_suggested_threshold?: { n: number; win_rate: number; avg_return_pct: number; expected_value_pct: number } | null;
};
export type OutcomesCalibration = { days: number; min_samples: number; calibrations: OutcomesCalibrationRow[] };
export type CalibrationBand = { band: string; midpoint: number; count: number; win_rate: number; win_rate_pct: number; avg_return_pct: number | null; calibration_gap: number };
export type CalibrationHorizon = { horizon: string; total: number; win_rate_pct: number; avg_return_pct: number | null; suggested_min_confidence: number | null; bands: CalibrationBand[] };
export type CalibrationData = { total: number; days: number; overall: { win_rate_pct: number; avg_return_pct: number | null }; horizons: CalibrationHorizon[] };
export type AlphaDecayCurvePoint = { day: number; avg_return_pct: number | null; p25: number | null; p75: number | null; n: number };
export type AlphaDecayReport = { horizon: string; signal_count: number; lookback_days: number; optimal_hold_days: number | null; optimal_return_pct: number | null; curve: AlphaDecayCurvePoint[] };
export type WalkForwardReport = {
  train_days: number; test_days: number; lookback_days: number; hold_days: number;
  windows: WalkForwardWindow[];
  total_windows: number; profitable_windows: number; signal_count: number;
  overall_accuracy: number | null; avg_return_pct: number | null;
  total_return_pct: number | null; sharpe: number | null; max_drawdown: number | null;
  benchmark: { symbol: string; windows: { end: string; equity: number; cumulative_return_pct: number }[]; total_return_pct: number } | null;
};
export type MLWeightValidation = { lookback_days: number; signal_count: number; optimal_weight: number | null; optimal_accuracy: number | null; current_formula_range: [number, number]; curve: MLWeightCurvePoint[] };
export type OptionsFlowContract = { expiry: string; side: 'call' | 'put'; strike: number; volume: number; oi: number; vol_oi: number; iv: number; itm: boolean; premium: number; is_whale: boolean };
export type OptionsFlow = { symbol: string; available: boolean; reason?: string; call_volume?: number; put_volume?: number; cp_ratio?: number; sentiment?: string; unusual_count?: number; unusual?: OptionsFlowContract[]; expiries_used?: string[]; whale_count?: number; top_whale_premium?: number };
export type QuickScanResult = { symbol: string; price: number; change_pct: number | null; change_5d: number | null; rsi: number | null; sma20: number | null; sma50: number | null; above_sma20: boolean | null; above_sma50: boolean | null; vol_ratio: number | null; range_pos_20d: number | null };
export type FearGreed = { score: number; rating: string; previous_close: number | null; previous_1_week: number | null; previous_1_month: number | null; previous_1_year: number | null; sp500_regime?: 'bull' | 'bear'; sp500_vs_ma200_pct?: number | null; components?: { vix: number; sp500_vs_ma: number; momentum: number; vix_spike: number } };
export type MarketBreadth = { breadth_pct: number | null; above_200ma: number; below_200ma: number; total: number; label: string; color: string; updated_at: string };

export type SRLevel = { price: number; strength: number; kind: 'support' | 'resistance' };
export type Levels = {
  support_resistance: SRLevel[];
  trendlines: { slope: number; intercept: number; r2: number; direction: string; start_idx: number; end_idx: number }[];
  fibonacci: Record<string, number>;
} | null;

export type Fundamentals = {
  // Valuation
  market_cap: number | null;
  enterprise_value: number | null;
  trailing_pe: number | null;
  forward_pe: number | null;
  price_to_book: number | null;
  ev_to_ebitda: number | null;
  ev_to_revenue: number | null;
  // Income statement
  total_revenue: number | null;
  gross_profit: number | null;
  net_income: number | null;
  ebitda: number | null;
  // Margins
  profit_margin: number | null;
  operating_margin: number | null;
  gross_margin: number | null;
  // Cash flow & balance sheet
  free_cashflow: number | null;
  operating_cashflow: number | null;
  total_cash: number | null;
  total_debt: number | null;
  // Per share
  trailing_eps: number | null;
  forward_eps: number | null;
  book_value: number | null;
  dividend_yield: number | null;
  dividend_rate: number | null;
  // Returns & risk
  return_on_equity: number | null;
  return_on_assets: number | null;
  revenue_growth: number | null;
  earnings_growth: number | null;
  beta: number | null;
  // Range & volume
  week_52_high: number | null;
  week_52_low: number | null;
  average_volume: number | null;
  shares_outstanding: number | null;
  // Analyst
  target_price: number | null;      // mean target
  target_high: number | null;
  target_low: number | null;
  target_median: number | null;
  recommendation: string | null;
  recommendation_mean: number | null;  // 1.0 strong buy → 5.0 sell
  number_of_analysts: number | null;
  analyst_strong_buy: number | null;
  analyst_buy: number | null;
  analyst_hold: number | null;
  analyst_underperform: number | null;
  analyst_sell: number | null;
  // Earnings calendar
  next_earnings_date: string | null;
  days_to_earnings: number | null;
  // Insider activity
  insider_buy_shares_6m: number | null;
  insider_sell_shares_6m: number | null;
  insider_buy_transactions_6m: number | null;
  insider_net_pct: number | null;
  // Individual analyst firm actions (last 90 days)
  analyst_actions: { date: string; firm: string; from_grade: string; to_grade: string; action: string }[];
  // Short interest
  short_percent_of_float: number | null;
  short_ratio: number | null;
  shares_short: number | null;
  shares_short_prior_month: number | null;
  // Ownership
  held_percent_institutions: number | null;
  held_percent_insiders: number | null;
  // Earnings surprise history
  eps_beat_rate: number | null;        // 0.0–1.0
  eps_avg_surprise_pct: number | null; // average % beat (positive = beating)
  eps_surprise_trend: string | null;   // "improving" | "declining" | "stable"
  eps_history: { quarter: string; actual: number | null; estimate: number | null; surprise_pct: number | null }[];
  // Data freshness
  fetched_at: string | null;
};

export type Overview = {
  price: Stock | null;
  prices: Price[] | null;
  indicators: { ts: string[]; values: Record<string, (number | null)[]> } | null;
  patterns: { patterns: { name: string; confidence: number; start_idx: number; end_idx: number }[] } | null;
  levels: Levels;
  signal: Signal | null;
  ranking: { score: number; fair_price?: number; technical: number; momentum: number; value: number; growth: number; volatility: number; relative_strength?: number | null; rs_rank?: number | null } | null;
  fundamentals: Fundamentals | null;
};

export type PositionTrade = {
  id: number;
  type: 'BUY' | 'SELL';
  shares: number;
  price: number;
  date: string;
};

export type UserPosition = {
  id: number;
  symbol: string;
  shares: number;
  avg_cost: number;
  currency: string;
  added_at: string;
  trades: PositionTrade[];
};

export type AppNotification = {
  id: number;
  alert_id: string;
  symbol: string;
  message: string;
  triggered_at: string;
  read: boolean;
  current_value?: number | null;
};

export type JournalTrade = {
  id: number;
  symbol: string;
  action: 'BUY' | 'SELL_SHORT';
  shares: number;
  entry_price: number;
  exit_price: number | null;
  entry_date: string;
  exit_date: string | null;
  stop_loss: number | null;
  take_profit: number | null;
  strategy: string | null;
  signal_confidence: number | null;
  notes: string | null;
  created_at: string;
};

export type JournalTradeIn = Omit<JournalTrade, 'id' | 'created_at'>;

export type SectorStock = {
  symbol: string;
  name: string;
  market: string;
  price: number | null;
  change_pct: number | null;
  market_cap: number | null;
};

export type SectorGroup = {
  sector: string;
  avg_change_pct: number | null;
  stock_count: number;
  total_mkt_cap?: number | null;
  stocks: SectorStock[];
};

export type EarningsItem = {
  symbol: string;
  name: string;
  sector: string | null;
  market: string;
  next_earnings_date: string;
  days_to_earnings: number;
  eps_estimate: number | null;
  trailing_eps: number | null;
  revenue_growth: number | null;
  earnings_growth: number | null;
  market_cap: number | null;
};

export type CalendarEvent = {
  type: 'earnings' | 'dividend' | 'split' | 'fomc' | 'cpi' | 'nfp' | 'pce' | 'gdp';
  date: string;
  days_to_event: number;
  title: string;
  description?: string | null;
  impact?: 'high' | 'medium' | 'low';
  symbol?: string | null;
  name?: string | null;
  sector?: string | null;
  market?: string | null;
  dividend_rate?: number | null;
  dividend_yield?: number | null;
  eps_estimate?: number | null;
  trailing_eps?: number | null;
  revenue_growth?: number | null;
  earnings_growth?: number | null;
  market_cap?: number | null;
};

export type AnalystRating = {
  symbol: string;
  name: string;
  sector: string | null;
  market: string;
  date: string;
  firm: string;
  from_grade: string;
  to_grade: string;
  action: string;
  target_price: number | null;
  recommendation: string | null;
};

export type SqueezeCandidate = {
  symbol: string;
  name: string;
  sector: string | null;
  market: string;
  short_percent_of_float: number;
  short_ratio: number | null;
  shares_short: number | null;
  shares_short_prior_month: number | null;
  price: number | null;
  change_pct: number | null;
  momentum_score: number | null;
  k_score: number | null;
  volume: number | null;
};

export type RelPerfPoint = {
  date: string;
  value: number;
  close: number;
};

export type DividendRecord = {
  date: string;
  amount: number;
};

export type DividendData = {
  symbol: string;
  dividends: DividendRecord[];
  annual_div_rate: number | null;
  dividend_yield: number | null;
  ex_dividend_date: number | null;
  payout_ratio: number | null;
  error?: string;
};

export type InstitutionalHolder = {
  holder: string;
  shares: number | null;
  date_reported: string | null;
  pct_out: number | null;
  value: number | null;
};

export type InstitutionalData = {
  symbol: string;
  held_pct_institutions: number | null;
  held_pct_insiders: number | null;
  float_shares: number | null;
  shares_outstanding: number | null;
  major_holders: Record<string, number | string>;
  institutional_holders: InstitutionalHolder[];
  error?: string;
};

export type ResearchRequestBody = {
  provider: string;
  model: string;
  api_key: string;
  portfolio_size?: number;
  max_risk_pct?: number;
};

export type ResearchSummary = {
  recommendation: 'STRONG BUY' | 'BUY' | 'WATCH' | 'AVOID' | 'SELL';
  overall_score: number;
  confidence: number;
  generated_at: string;
};

export type ChecklistItem = { item: string; status: 'pass' | 'warning' | 'fail'; note?: string };

export type ResearchReport = {
  symbol: string;
  company_name: string;
  generated_at: string;
  report_quality: 'full' | 'partial' | 'fallback';
  current_price: number | null;
  market_cap: number | null;
  sector: string | null;
  industry: string | null;
  recommendation: 'STRONG BUY' | 'BUY' | 'WATCH' | 'AVOID' | 'SELL';
  overall_score: number;
  confidence: number;
  scores: { technical: number; fundamental: number; company: number; industry: number; economic: number };
  executive_summary: {
    bullish_factors: string[];
    bearish_factors: string[];
    key_risks: string[];
    key_opportunities: string[];
  };
  technical: {
    score: number;
    trend_verdict: string;
    price_vs_50_ema: { value: string; ema: number | null; pct_diff: number | null; interpretation: string };
    price_vs_200_ema: { value: string; ema: number | null; pct_diff: number | null; interpretation: string };
    cross_status: string;
    rsi: { value: number | null; status: string; interpretation: string };
    macd: { line: number | null; signal: number | null; histogram: number | null; crossover: string; interpretation: string };
    histogram_analysis: { value: number | null; status: string; interpretation: string };
    volume: { current: number; avg_20d: number; rvol: number; status: string; interpretation: string };
    support_resistance: { nearest_support: number | null; major_support: number | null; nearest_resistance: number | null; major_resistance: number | null };
    atr: { value: number | null; pct: number | null; volatility_rating: string };
    entry_planning: {
      aggressive_entry: { zone: string; rationale: string };
      conservative_entry: { zone: string; rationale: string };
      stop_loss: { price: number | null; method: string; rationale: string };
      take_profit: { target: number; price: number; gain_pct: number; rationale: string }[];
      risk_reward: { expected_reward: number | null; expected_risk: number | null; ratio: number | null; assessment: string };
    };
  };
  fundamental: {
    score: number;
    revenue: { yoy_growth: number | null; assessment: string };
    eps: { yoy_growth: number | null; trailing_eps: number | null; forward_eps: number | null; assessment: string };
    margins: { gross: number | null; operating: number | null; net: number | null; comparison: string };
    balance_sheet: { cash: number; debt: number; de_ratio: number | null; assessment: string };
    cash_flow: { operating_cf: number | null; fcf: number | null; fcf_margin: number | null; assessment: string };
    valuation: { pe: number | null; forward_pe: number | null; peg: number | null; peg_growth_source: string | null; price_sales: number | null; ev_ebitda: number | null; assessment: string };
    profitability: { roe: number | null; roa: number | null; grade: string };
  };
  company: {
    business_model: string;
    competitive_advantage: Record<string, string>;
    moat: { rating: string; explanation: string };
    insider_activity: { status: string; explanation: string };
    institutional_ownership: { pct: number; trend: string; interpretation: string };
    management: { rating: string; explanation: string };
  };
  industry_analysis: {
    status: string;
    evidence: string;
    tam: { size: string; growth: string; expansion_potential: string; rating: string };
    market_share: { position: string; trend: string; verdict: string };
    competitors: { name: string; relative_position: string }[];
    regulatory_risk: string;
    verdict: string;
    verdict_explanation: string;
  };
  economic: {
    fed: { status: string; impact: string };
    inflation: { cpi_trend: string; impact: string };
    gdp: { status: string; significance: string };
    employment: { status: string };
    recession_risk: { yield_curve_inverted: boolean; gdp_negative: boolean; unemployment_rising: boolean; consumer_confidence_falling: boolean; rating: string };
    market_environment: { favored_style: string; explanation: string };
  };
  checklist: {
    layer1_company: ChecklistItem[];
    layer2_industry: ChecklistItem[];
    layer3_economy: ChecklistItem[];
    layer4_technical: ChecklistItem[];
  };
  entry_planning: ResearchReport['technical']['entry_planning'];
  position_sizing: { portfolio_size: number; max_risk_pct: number; dollar_risk: number | null; stop_distance: number | null; share_quantity: number | null; position_size: number | null; pct_of_portfolio: number | null };
  trade_invalidation: string[];
  ai_verdict: { can_buy_today: string; why: string; biggest_risks: string[]; must_improve: string[]; strong_buy_catalysts: string[]; confidence_pct: number; final_recommendation: string };
  signal: { signal: string | null; confidence: number | null; horizon: string | null };
  ranking: { score: number | null; rank: number | null; technical: number | null; momentum: number | null; value: number | null; growth: number | null };
  analyst: { target_price: number | null; target_high: number | null; target_low: number | null; recommendation: string | null; num_analysts: number | null };
  beta: number | null;
  week_52_high: number | null;
  week_52_low: number | null;
  short_float_pct: number | null;
  next_earnings: string | null;
  days_to_earnings: number | null;
};

export type AdminSignalLogItem = {
  id: number;
  symbol: string;
  name: string;
  market: string;
  signal: string;
  horizon: string;
  confidence: number;
  bullish_probability: number | null;
  reasons: Record<string, unknown> | null;
  source: string;
  generated_at: string;
  outcome_pct: number | null;
  is_correct: boolean | null;
  entry_price: number | null;
  exit_price: number | null;
  exit_date: string | null;
};

export type AdminSignalLogResponse = {
  total: number;
  page: number;
  limit: number;
  pages: number;
  items: AdminSignalLogItem[];
};

// ── WF-2 Paper Portfolio types ────────────────────────────────────────────────

export type PaperPortfolioListItem = {
  id: number;
  name: string;
  trading_style: string;
  market: string;
  current_equity: number;
  initial_capital: number;
  total_return_pct: number;
  win_rate_pct: number;
  open_positions: number;
  closed_trades: number;
  sharpe: number | null;
  sortino: number | null;
  cagr_pct: number | null;
  max_drawdown_pct: number | null;
  is_active: boolean;
  is_running: boolean;
  is_paused: boolean;
  created_at: string | null;
  entry_gate_block: { gate: string; reason: string; ts: string } | null;
};

export type PaperTradeParamResult = {
  stop_pct?: number;
  tp_pct?: number;
  max_hold_days?: number;
  best_stop_pct?: number;
  best_tp_pct?: number;
  best_max_hold_days?: number;
  best_sharpe?: number;
  n_trades?: number;
  tuned_at?: string;
  is_tuned: boolean;
  is_running: boolean;
  note?: string;
};

export type PaperCompareData = {
  portfolio_id: number;
  name: string;
  trading_style: string;
  initial_capital: number;
  curve: { date: string; equity: number; spy_close: number | null; market_regime: string | null }[];
};

export type PaperPortfolioConfig = {
  trading_style: string;
  enabled: boolean;
  paused?: boolean;
  // Position limits
  max_positions: number;
  max_market_positions?: number;
  max_sector_positions?: number;
  max_entries_per_day?: number;
  max_open_exposure_pct?: number;
  max_positions_per_symbol_global?: number;
  equity_floor_pct?: number;
  // Entry quality
  min_confidence: number;
  min_kscore: number;
  min_rr_ratio: number;
  min_entry_score: number;
  min_ta_score?: number;
  min_volume_z?: number;
  max_entry_gap_pct?: number;
  // Risk / sizing
  risk_per_trade_pct: number;
  max_position_pct: number;
  max_sector_pct: number;
  // Exit management
  max_hold_days: number;
  hold_stall_days?: number;
  wait_exit_days: number;
  trail_atr_mult: number;
  trail_trigger_pct: number;
  breakeven_trigger_pct: number;
  partial_tp_pct: number;
  partial_tp2_pct?: number;
  stop_cooldown_hours?: number;
  // Circuit breakers
  max_daily_loss_pct?: number;
  max_weekly_loss_pct?: number;
  max_portfolio_drawdown_pct?: number;
  max_consecutive_losses?: number;
};

export type PaperPortfolioSummary = {
  portfolio_id: number;
  name: string;
  trading_style: string;
  initial_capital: number;
  current_equity: number;
  current_cash: number;
  open_positions_value: number;
  total_return_pct: number;
  total_realized_pnl: number;
  total_unrealized_pnl: number;
  open_positions: number;
  closed_trades: number;
  win_rate_pct: number;
  avg_win_pct: number;
  avg_loss_pct: number;
  profit_factor: number | null;
  avg_hold_days: number | null;
  expectancy_pct: number | null;
  sharpe: number | null;
  sortino: number | null;
  cagr_pct: number | null;
  max_drawdown_pct: number | null;
  calmar: number | null;
  data_days: number;
  insufficient_data: boolean;
  outperformance_vs_spy: number | null;
  outperformance_vs_qqq: number | null;
  outperformance_vs_hsi: number | null;
  spy_close: number | null;
  qqq_close: number | null;
  regime_state: 'bull' | 'neutral' | 'choppy' | 'risk_off' | 'bear' | null;
  regime_vix: number | null;
  regime_spy: number | null;
  regime_notes: string[];
  alpha: number | null;
  beta: number | null;
  info_ratio: number | null;
  config: PaperPortfolioConfig;
  created_at: string | null;
  exit_breakdown: Record<string, number>;
};

export type PaperPosition = {
  id: number;
  symbol: string;
  trading_style: string;
  entry_date: string | null;
  entry_price: number;
  current_price: number | null;
  shares: number;
  position_value: number;
  stop_loss: number;
  current_stop: number;
  take_profit: number | null;
  highest_price: number | null;
  hold_days: number;
  unrealized_pnl: number;
  unrealized_pct: number;
  rr_ratio_at_entry: number | null;
  entry_score: number | null;
  confidence_at_entry: number | null;
  kscore_at_entry: number | null;
  market_regime_at_entry: string | null;
  sector: string | null;
  decision_notes: string[];
  entry_reasons: Record<string, unknown>;
  current_signal: string | null;
};

export type PaperTrade = {
  id: number;
  symbol: string;
  trading_style: string;
  entry_date: string | null;
  entry_time: string | null;
  entry_price: number;
  exit_time: string | null;
  exit_price: number | null;
  exit_reason: string | null;
  shares: number;
  pnl: number | null;
  pct_return: number | null;
  hold_days: number;
  stop_loss: number;
  take_profit: number | null;
  rr_ratio_at_entry: number | null;
  entry_score: number | null;
  confidence_at_entry: number | null;
  kscore_at_entry: number | null;
};

export type PaperTradesResponse = {
  total: number;
  page: number;
  limit: number;
  pages: number;
  items: PaperTrade[];
};

export type PaperEquityPoint = {
  date: string;
  equity: number;
  cash: number;
  open_positions_value: number;
  open_positions_count: number;
  spy_close: number | null;
  qqq_close: number | null;
  hsi_close: number | null;
  market_regime: string | null;  // PT-A2: for regime shading overlay
};

export type PaperDecisionItem = {
  id: number;
  symbol: string;
  trading_style: string;
  decision: string;
  entry_time: string | null;
  entry_price: number;
  entry_score: number | null;
  decision_notes: string[];
  confidence_at_entry: number | null;
  kscore_at_entry: number | null;
  rr_ratio_at_entry: number | null;
  market_regime_at_entry: string | null;
  stage: string;
  exit_time: string | null;
  exit_price: number | null;
  exit_reason: string | null;
  entry_reasons: Record<string, unknown>;
  exit_reasons: Record<string, unknown>;
  hold_days: number;
  stop_loss: number;
  take_profit: number | null;
  shares: number;
  pnl: number | null;
  pct_return: number | null;
};

export type PaperDecisionsResponse = {
  total: number;
  page: number;
  limit: number;
  pages: number;
  items: PaperDecisionItem[];
};

export type SchedulerJob = {
  job: string;
  status: 'ok' | 'error' | 'skipped';
  last_run: string;
  duration_s: number;
  error: string | null;
};

export type ServiceHealthResult = {
  service: string;
  status: 'ok' | 'error' | 'timeout';
  latency_ms: number;
  code: number | null;
  error?: string;
};

export type ServiceHealthReport = {
  gateway: string;
  services_ok: number;
  services_total: number;
  results: ServiceHealthResult[];
};

export type MlModelMetric = {
  symbol: string;
  model: string;
  test_auc: number | null;
  cv_auc: number | null;
  accuracy: number | null;
  overfit_gap: number | null;
  buy_threshold: number | null;
  error?: string;
};

export type MlMetricsList = {
  model: string;
  count: number;
  symbols: MlModelMetric[];
};

export type FeatureImportanceItem = {
  name: string;
  importance: number;
  category: 'fundamental' | 'macro' | 'technical';
};

export type FeatureImportanceResult = {
  symbol: string;
  model: string;
  features: FeatureImportanceItem[];
  trained_at: string | null;
};

// ── Broker integration ────────────────────────────────────────────────────────

export type BrokerType = 'etrade' | 'etrade_sandbox' | 'fidelity_manual';

export type BrokerConnection = {
  id: number;
  name: string;
  broker_type: BrokerType;
  account_id: string | null;
  is_active: boolean;
  is_authorized: boolean;
};

export type CreateBrokerConnectionPayload = {
  name: string;
  broker_type: BrokerType;
  consumer_key?: string;
  consumer_secret?: string;
  account_number?: string;
  notes?: string;
};

export type BrokerAccountInfo = {
  account_id: string;
  broker_type: string;
  cash_available: number;
  equity: number;
  buying_power: number;
  positions: {
    symbol: string;
    qty: number;
    avg_cost: number;
    market_value: number;
    unrealized_pnl: number;
    unrealized_pnl_pct: number;
  }[];
};

// ── Decision Engine types ────────────────────────────────────────────────────

export type DecisionVerdict = 'BUY' | 'SCALE' | 'HOLD' | 'SKIP' | 'BLOCKED';

export type ScoreItem = { layer: string; pts: number; note: string };

export type PositionPlan = {
  shares: number;
  size_pct: number;
  dollar_risk: number;
  entry_price: number;
  stop_price: number;
  target_1: number;
  target_2: number;
  rr_ratio: number;
};

export type DecisionFactors = {
  signal_direction: string | null;
  signal_confidence: number | null;
  ml_bull_prob: number | null;
  research_recommendation: string | null;
  research_score: number | null;
  regime: string | null;
  volume_z: number | null;
  days_to_earnings: number | null;
  signal_age_h: number | null;
  conf_delta: number | null;
  cross_style_buys: number | null;
};

export type DecisionMultipliers = {
  regime: number;
  research: number;
  confidence: number;
  consensus: number;
  earnings: number;
};

export type DecisionResult = {
  symbol: string;
  style: string;
  verdict: DecisionVerdict;
  score: number;
  min_score: number;
  position: PositionPlan | null;
  factors: DecisionFactors;
  multipliers: DecisionMultipliers;
  score_breakdown: ScoreItem[];
  blocked_reason: string | null;
  latency_ms: number;
  timestamp: string;
  explanation?: string;
  result?: DecisionResult;
};

export type RegimeStatus = {
  state: 'bull' | 'neutral' | 'choppy' | 'risk_off' | 'bear';
  vix: number | null;
  vix9d: number | null;
  spy_price: number | null;
  spy_ema20: number | null;
  spy_ema50: number | null;
  spy_ema200: number | null;
  spy_20d_ret: number | null;
  qqq_price: number | null;
  qqq_ema50: number | null;
  vix_5d_trend: 'rising' | 'falling' | 'flat' | null;
  vix_term_inverted: boolean;
  breadth_weak: boolean;
  breadth_size_mult: number;
  hsi_price: number | null;
  hsi_ema50: number | null;
  hsi_ema200: number | null;
  notes: string[];
};

export type DeDivergenceEvent = {
  ts: string;
  symbol: string;
  paper_enter: boolean;
  paper_score: number;
  de_verdict: string;
  de_score: number;
  de_min_score: number;
  de_blocked_reason: string | null;
};

export type DeAgreementEvent = {
  ts: string;
  symbol: string;
  verdict: string;
  paper_enter: boolean;
  de_score: number;
  paper_score: number;
};

export type DeDivergenceResponse = {
  total_divergences: number;
  total_agreements: number;
  agreement_rate_pct: number | null;
  divergences: DeDivergenceEvent[];
  agreements: DeAgreementEvent[];
};

// ── Event Intelligence types ─────────────────────────────────────────────────

export type EconomicEvent = {
  id: number;
  event_name: string;
  event_type: string;
  market: string;
  event_date: string;
  event_time: string | null;
  previous_value: number | null;
  forecast_value: number | null;
  actual_value: number | null;
  impact_level: string | null;
  notes: string | null;
};

export type EconomicEventsResponse = {
  events: EconomicEvent[];
  fomc_days_away: number | null;
};

export type EarningsEvent = {
  id: number;
  symbol: string;
  earnings_date: string;
  estimated_eps: number | null;
  actual_eps: number | null;
  estimated_revenue: number | null;
  actual_revenue: number | null;
  beat_rate: number | null;
  avg_beat_pct: number | null;
  surprise_pct: number | null;
  is_upcoming: boolean;
};

export type InsiderTransaction = {
  id: number;
  symbol: string;
  insider_name: string;
  insider_role: string;
  transaction_type: string;
  shares: number | null;
  price_per_share: number | null;
  total_value: number | null;
  transaction_date: string;
  filing_date: string | null;
};

export type InsiderResponse = {
  symbol: string;
  score: number;
  transactions: InsiderTransaction[];
};

export type InsiderLeaderItem = {
  symbol: string;
  score: number;
  buy_count: number;
  sell_count: number;
  net_value: number | null;
};

export type CongressTrade = {
  id: number;
  symbol: string;
  politician_name: string;
  chamber: string;
  party: string | null;
  transaction_type: string;
  amount_range: string | null;
  transaction_date: string;
  disclosure_date: string | null;
  asset_description: string | null;
};

export type CongressResponse = {
  symbol: string;
  score: number;
  trades: CongressTrade[];
};

export type CongressLeaderItem = {
  symbol: string;
  score: number;
  buy_count: number;
  sell_count: number;
  politician_count: number;
};

export type InstitutionalHolding = {
  fund_name: string;
  shares: number | null;
  value_usd: number | null;
  pct_portfolio: number | null;
  change_shares: number | null;
  filing_date: string;
};

export type InstitutionalResponse = {
  symbol: string;
  score: number;
  fund_count: number;
  total_value_usd: number | null;
  holdings: InstitutionalHolding[];
};

export type PoliticalEvent = {
  id: number;
  symbol: string | null;
  company_name: string;
  agency: string | null;
  contract_amount: number | null;
  award_date: string;
  description: string | null;
  sector: string | null;
};

export type CatalystScore = {
  symbol: string;
  earnings_score: number | null;
  insider_score: number | null;
  congress_score: number | null;
  institutional_score: number | null;
  political_score: number | null;
  catalyst_score: number | null;
  risk_score: number | null;
  composite_score: number | null;
  updated_at: string;
};

export type CatalystLeaderItem = {
  symbol: string;
  score: number;
};

export type EventIntelOverview = {
  economic: { upcoming_count: number; fomc_days_away: number | null };
  earnings: { upcoming_count: number };
  insider: { top_buys: InsiderLeaderItem[] };
  congress: { top_buys: CongressLeaderItem[] };
  catalyst_leaders: CatalystLeaderItem[];
  risk_leaders: CatalystLeaderItem[];
  composite_leaders: CatalystLeaderItem[];
};

export type QuarterlyRow = {
  date: string;
  revenue: number | null;
  gross_profit: number | null;
  net_income: number | null;
  ebitda: number | null;
};
