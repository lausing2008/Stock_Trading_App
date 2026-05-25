const BASE = process.env.NEXT_PUBLIC_API_URL ?? '/api';

function authHeader(): Record<string, string> {
  if (typeof window === 'undefined') return {};
  const token = localStorage.getItem('stockai_jwt')?.trim() || null;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { 'content-type': 'application/json', ...authHeader(), ...(init?.headers ?? {}) },
  });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  if (r.status === 204 || r.headers.get('content-length') === '0') return undefined as T;
  return (await r.json()) as T;
}

export const api = {
  listStocks: (market?: string) => request<Stock[]>(`/stocks${market ? `?market=${market}` : ''}`),
  latestPrices: () => request<LatestPrice[]>(`/stocks/latest_prices`),
  getStock: (symbol: string) => request<Stock>(`/stocks/${symbol}`),
  getPrices: (symbol: string, tf = '1d', limit = 400) =>
    request<Price[]>(`/stocks/${symbol}/prices?timeframe=${tf}&limit=${limit}`),
  overview: (symbol: string) => request<Overview>(`/aggregate/overview/${symbol}`),
  refreshFundamentals: (symbol: string) => request<unknown>(`/stocks/${symbol}/fundamentals?refresh=true`),
  rankings: (market?: string) => {
    const params = new URLSearchParams({ limit: '500' });
    if (market) params.set('market', market);
    return request<{ rankings: RankingRow[] }>(`/rankings?${params}`);
  },
  signal: (symbol: string) => request<Signal>(`/signals/${symbol}`),
  allSignals: () => request<SignalSummary[]>(`/signals`),
  refreshSignal: (symbol: string) => request<Signal>(`/signals/${symbol}?persist=true`),
  refreshSignals: (market?: string) => request<{ refreshed: number }>(`/signals/refresh`, { method: 'POST', body: JSON.stringify(market ? { market } : {}) }),
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
  optimizePortfolio: (body: unknown) => request<PortfolioWeights>(`/portfolio/optimize`, { method: 'POST', body: JSON.stringify(body) }),
  ingest: (symbols: string[], force = false) => request<{ status: string; symbols?: number }>(`/admin/ingest`, { method: 'POST', body: JSON.stringify({ symbols, force }) }),
  trainAll: () => request<{ status: string; count: number; symbols: string[] }>(`/ml/train_all`, { method: 'POST' }),
  addStock: (symbol: string) => request<{ status: string; symbol: string; name: string; sector?: string }>(`/admin/add_stock`, { method: 'POST', body: JSON.stringify({ symbol }) }),
  deleteStock: (symbol: string) => request<{ status: string; symbol: string }>(`/admin/stocks/${symbol}`, { method: 'DELETE' }),
  marketOverview: () => request<MarketIndex[]>(`/stocks/market_overview`),
  fearGreed: () => request<FearGreed>(`/stocks/fear_greed`),
  listWatchlists: () => request<WatchlistMeta[]>(`/watchlists`),
  createWatchlist: (name: string) => request<WatchlistMeta>(`/watchlists`, { method: 'POST', body: JSON.stringify({ name }) }),
  renameWatchlist: (id: number, name: string) => request<WatchlistMeta>(`/watchlists/${id}`, { method: 'PUT', body: JSON.stringify({ name }) }),
  deleteWatchlist: (id: number) => request(`/watchlists/${id}`, { method: 'DELETE' }),
  listWatchlist: (listId?: number) => request<WatchlistItem[]>(`/watchlist${listId != null ? `?list_id=${listId}` : ''}`),
  addToWatchlist: (symbol: string, listId?: number) => request<WatchlistItem>(`/watchlist/${symbol}${listId != null ? `?list_id=${listId}` : ''}`, { method: 'POST' }),
  removeFromWatchlist: (symbol: string, listId?: number) => request(`/watchlist/${symbol}${listId != null ? `?list_id=${listId}` : ''}`, { method: 'DELETE' }),
  isWatched: async (symbol: string): Promise<boolean> => {
    const items = await request<WatchlistItem[]>(`/watchlist`);
    return items.some(i => i.symbol === symbol);
  },

  // Price alerts
  listAlerts: () => request<PriceAlert[]>(`/alerts`),
  createAlert: (body: { symbol: string; condition: string; threshold: number; email?: string; note?: string }) =>
    request<PriceAlert>(`/alerts`, { method: 'POST', body: JSON.stringify(body) }),
  deleteAlert: (id: number) => request(`/alerts/${id}`, { method: 'DELETE' }),

  // Signal alerts
  listSignalAlerts: () => request<SignalAlertItem[]>(`/signal-alerts`),
  createSignalAlert: (symbol: string, email?: string) =>
    request<SignalAlertItem>(`/signal-alerts`, { method: 'POST', body: JSON.stringify({ symbol, email }) }),
  deleteSignalAlert: (id: number) => request(`/signal-alerts/${id}`, { method: 'DELETE' }),

  // User profile
  getMe: () => request<AppUser>(`/auth/me`),
  updateProfile: (body: { email?: string }) =>
    request<AppUser>(`/auth/me`, { method: 'PUT', body: JSON.stringify(body) }),

  // User management (admin)
  listUsers: () => request<AppUser[]>(`/auth/users`),
  impersonate: (username: string) => request<{ token: string; username: string; role: string }>(`/auth/impersonate/${username}`, { method: 'POST' }),
  createUser: (username: string, password: string, role: string) =>
    request<AppUser>(`/auth/users`, { method: 'POST', body: JSON.stringify({ username, password, role }) }),
  deleteUser: (username: string) => request(`/auth/users/${username}`, { method: 'DELETE' }),
  adminResetPassword: (username: string, newPassword: string) =>
    request(`/auth/users/${username}/reset-password`, { method: 'PUT', body: JSON.stringify({ new_password: newPassword }) }),
  toggleUser: (username: string) => request<{ is_active: boolean }>(`/auth/users/${username}/toggle`, { method: 'PUT' }),
  pushConfig: (keys: { polygon_api_key?: string; alpha_vantage_api_key?: string; quiver_api_key?: string }) =>
    request<{ status: string }>(`/admin/config`, { method: 'POST', body: JSON.stringify(keys) }),

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
    return request<CongressTrade[]>(`/congress/trades?${params}`);
  },

  // Signal accuracy tracker
  signalAccuracy: (lookbackDays = 90, symbol?: string) => {
    const params = new URLSearchParams({ lookback_days: String(lookbackDays) });
    if (symbol) params.set('symbol', symbol);
    return request<SignalAccuracyReport>(`/signals/accuracy?${params}`);
  },
  resetSignals: () => request<{ status: string; deleted: number; repersisting: number }>('/signals/reset', { method: 'POST' }),

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

export type Signal = {
  symbol: string;
  signal: 'BUY' | 'SELL' | 'HOLD' | 'WAIT';
  horizon: string;
  confidence: number;
  bullish_probability: number;
  reasons: Record<string, unknown>;
};

export type SignalSummary = { symbol: string; signal: 'BUY' | 'SELL' | 'HOLD' | 'WAIT'; horizon: string; confidence: number; bullish_probability: number | null; ts: string | null };
export type RankingRow = { symbol: string; name: string; name_zh?: string | null; score: number | null; market: string; fair_price?: number | null; sector?: string | null; technical?: number | null; momentum?: number | null; value?: number | null; growth?: number | null; volatility?: number | null };
export type Prediction = { symbol: string; bullish_probability: number; confidence: number; direction: string };
export type Backtest = {
  backtest_id: number;
  total_return: number;
  cagr: number;
  sharpe: number;
  max_drawdown: number;
  win_rate: number;
  profit_factor: number;
  n_trades: number;
  equity_curve: { ts: string; equity: number }[];
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
export type LatestPrice = { symbol: string; price: number; prev_close: number | null; change_pct: number | null; currency: string };
export type MarketIndex = { name: string; ticker: string; market: string; price: number | null; change_pct: number | null };
export type WatchlistItem = { symbol: string; name: string; name_zh?: string | null; market: string; exchange: string; sector?: string; currency: string; added_at: string };
export type WatchlistMeta = { id: number; name: string; item_count: number; created_at: string };
export type NewsItem = { title: string; url: string; source: string; published_at: number; sentiment: number; sentiment_label: 'bullish' | 'bearish' | 'neutral'; thumbnail?: string };
export type AppUser = { id: number; username: string; role: 'admin' | 'user'; is_active: boolean; email?: string | null; created_at: string };
export type PriceAlert = { id: number; symbol: string; condition: string; threshold: number; email: string; note: string | null; triggered: boolean; triggered_at: string | null; created_at: string };
export type SignalAlertItem = { id: number; symbol: string; email: string | null; last_signal: string | null; created_at: string };
export type CongressTrade = { Ticker: string; Date: string; Politician: string; Transaction: string; Min: number | null; Max: number | null; Party: string | null; State: string | null; Chamber: string | null; ReportDate: string | null };
export type SignalAccuracyRow = { symbol: string; name: string; signal: 'BUY' | 'SELL'; confidence: number; bullish_probability: number | null; signal_date: string; entry_price: number; exit_price: number; pct_change: number; correct: boolean; days_held: number };
export type SignalAccuracyReport = { lookback_days: number; total_signals: number; buy_count: number; sell_count: number; buy_accuracy: number | null; sell_accuracy: number | null; overall_accuracy: number | null; avg_buy_return_pct: number | null; avg_sell_return_pct: number | null; profit_factor: number | null; signals: SignalAccuracyRow[] };
export type QuickScanResult = { symbol: string; price: number; change_pct: number | null; change_5d: number | null; rsi: number | null; sma20: number | null; sma50: number | null; above_sma20: boolean | null; above_sma50: boolean | null; vol_ratio: number | null; range_pos_20d: number | null };
export type FearGreed = { score: number; rating: string; previous_close: number | null; previous_1_week: number | null; previous_1_month: number | null; previous_1_year: number | null; sp500_regime?: 'bull' | 'bear'; sp500_vs_ma200_pct?: number | null; components?: { vix: number; sp500_vs_ma: number; momentum: number; vix_spike: number } };

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
};

export type Overview = {
  price: Stock | null;
  prices: Price[] | null;
  indicators: { ts: string[]; values: Record<string, (number | null)[]> } | null;
  patterns: { patterns: { name: string; confidence: number; start_idx: number; end_idx: number }[] } | null;
  levels: Levels;
  signal: Signal | null;
  ranking: { score: number; fair_price?: number; technical: number; momentum: number; value: number; growth: number; volatility: number } | null;
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
