const SETTINGS_KEY = 'stockai_settings';

export type AppSettings = {
  priceRefreshInterval: number;   // seconds
  newsMaxAgeDays: number;
  defaultMlModel: string;
  alertCooldownMinutes: number;
  notificationSound: boolean;
  defaultChartLimit: number;
};

export const DEFAULT_SETTINGS: AppSettings = {
  priceRefreshInterval: 60,
  newsMaxAgeDays: 7,
  defaultMlModel: 'xgboost',
  alertCooldownMinutes: 60,
  notificationSound: true,
  defaultChartLimit: 400,
};

export function loadSettings(): AppSettings {
  if (typeof window === 'undefined') return DEFAULT_SETTINGS;
  try {
    const stored = localStorage.getItem(SETTINGS_KEY);
    return stored ? { ...DEFAULT_SETTINGS, ...JSON.parse(stored) } : DEFAULT_SETTINGS;
  } catch { return DEFAULT_SETTINGS; }
}

export function saveSettings(s: Partial<AppSettings>): AppSettings {
  const merged = { ...loadSettings(), ...s };
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(merged));
  window.dispatchEvent(new CustomEvent('stockai:settings', { detail: merged }));
  return merged;
}
