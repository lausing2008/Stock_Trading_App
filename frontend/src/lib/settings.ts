import { storage } from './storage';

const SETTINGS_KEY = 'settings';

export type AppSettings = {
  // Data & Refresh
  priceRefreshInterval: number;
  newsMaxAgeDays: number;
  defaultChartLimit: number;

  // Notifications
  notificationSound: boolean;
  alertCooldownMinutes: number;

  // ML
  defaultMlModel: string;

  // Data Sources — stock price/OHLCV
  dataSourceYfinance: boolean;
  dataSourceAlphaVantage: boolean;
  alphaVantageApiKey: string;
  dataSourcePolygon: boolean;
  polygonApiKey: string;

  // Data Sources — news
  newsSourceYfinance: boolean;
  newsSourceGoogleNews: boolean;

  // AI Assistant
  aiProvider: 'claude' | 'deepseek' | 'none';
  claudeApiKey: string;
  claudeModel: string;
  deepseekApiKey: string;
  deepseekModel: string;
};

export const DEFAULT_SETTINGS: AppSettings = {
  priceRefreshInterval: 60,
  newsMaxAgeDays: 7,
  defaultChartLimit: 400,
  notificationSound: true,
  alertCooldownMinutes: 60,
  defaultMlModel: 'xgboost',

  dataSourceYfinance: true,
  dataSourceAlphaVantage: false,
  alphaVantageApiKey: '',
  dataSourcePolygon: false,
  polygonApiKey: '',

  newsSourceYfinance: true,
  newsSourceGoogleNews: true,

  aiProvider: 'none',
  claudeApiKey: '',
  claudeModel: 'claude-sonnet-4-6',
  deepseekApiKey: '',
  deepseekModel: 'deepseek-chat',
};

export function loadSettings(): AppSettings {
  if (typeof window === 'undefined') return DEFAULT_SETTINGS;
  try {
    const stored = storage.getItem(SETTINGS_KEY);
    return stored ? { ...DEFAULT_SETTINGS, ...JSON.parse(stored) } : DEFAULT_SETTINGS;
  } catch { return DEFAULT_SETTINGS; }
}

export function saveSettings(s: Partial<AppSettings>): AppSettings {
  const merged = { ...loadSettings(), ...s };
  storage.setItem(SETTINGS_KEY, JSON.stringify(merged));
  window.dispatchEvent(new CustomEvent('stockai:settings', { detail: merged }));
  return merged;
}

/** Returns the comma-separated news source string for API requests. */
export function activeNewsSources(s?: AppSettings): string {
  const cfg = s ?? loadSettings();
  const parts: string[] = [];
  if (cfg.newsSourceYfinance) parts.push('yfinance');
  if (cfg.newsSourceGoogleNews) parts.push('google');
  return parts.length ? parts.join(',') : 'yfinance';
}
