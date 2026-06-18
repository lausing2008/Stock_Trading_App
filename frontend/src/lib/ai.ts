/**
 * AI provider abstraction for StockAI.
 *
 * There is ONE global AI provider configured by the user in Settings → AI
 * Assistant. Every AI feature in the app calls `askAI()` from this file —
 * none of them hardcode Claude or DeepSeek. The active provider is stored in
 * localStorage via `AppSettings.aiProvider`.
 *
 * ┌─────────────────────────────────┬────────────────────────────────────────┐
 * │ Feature / Page                  │ What AI does                           │
 * ├─────────────────────────────────┼────────────────────────────────────────┤
 * │ /forecast                       │ 1. Suggests a universe of tickers       │
 * │                                 │ 2. Ranks them into 10 swing trade picks │
 * │                                 │    with entry/stop/target + game plan   │
 * │                                 │ temperature=0 (deterministic output)    │
 * ├─────────────────────────────────┼────────────────────────────────────────┤
 * │ /opportunities                  │ Generates swing trade picks from        │
 * │                                 │ BUY-signal stocks with K-Score data     │
 * │                                 │ temperature=0.2 (default)               │
 * ├─────────────────────────────────┼────────────────────────────────────────┤
 * │ /stock/[symbol] — Game Plan     │ Produces a 10-day trade plan with       │
 * │                                 │ entry/stop/target based on live S/R     │
 * │                                 │ levels, fundamentals, and signal data   │
 * │                                 │ temperature=0.2 (default)               │
 * ├─────────────────────────────────┼────────────────────────────────────────┤
 * │ /stock/[symbol] — AI Chat       │ Free-form Q&A about the stock using     │
 * │                                 │ live price, signal, and fundamental     │
 * │                                 │ data as context                         │
 * │                                 │ temperature=0.2 (default)               │
 * ├─────────────────────────────────┼────────────────────────────────────────┤
 * │ /insider                        │ Fallback when no Quiver API key is set: │
 * │                                 │ asks AI for recent congressional trades │
 * │                                 │ by Pelosi, Josh, and Mark Green         │
 * │                                 │ temperature=0.2 (default)               │
 * └─────────────────────────────────┴────────────────────────────────────────┘
 *
 * Provider options (configured in Settings):
 *   - Claude (Anthropic) — Sonnet 4.6 recommended; Opus 4.7 most capable;
 *     Haiku 4.5 fastest/cheapest
 *   - DeepSeek — deepseek-chat recommended; deepseek-reasoner (R1) for
 *     complex reasoning tasks
 *
 * Request flow:
 *   askAI() → POST /api/ai/chat (Next.js proxy / nginx)
 *           → api-gateway /ai/chat (ai_proxy.py)
 *           → Anthropic API  (Claude)
 *              or DeepSeek API (DeepSeek)
 *
 * Temperature guide:
 *   0   — fully deterministic; use for structured JSON outputs (forecast picks)
 *   0.2 — very consistent with slight phrasing variation; default for all calls
 *   1.0 — highly creative/random; was the old default before v3, caused
 *         forecast results to differ on every regeneration
 */
import { loadSettings } from './settings';

export type AiMessage = { role: 'user' | 'assistant'; content: string };

const PROVIDER_LABELS: Record<string, string> = {
  claude: 'Claude',
  deepseek: 'DeepSeek',
};

/** Returns the display name of the currently configured AI provider. */
export function getAiProviderLabel(): string {
  return PROVIDER_LABELS[loadSettings().aiProvider] ?? 'AI';
}

/**
 * Returns true if a provider is selected.
 * The backend will fall back to the admin-configured shared key in Redis
 * if the user hasn't set their own key, so we don't gate on key presence here.
 */
export function isAiConfigured(): boolean {
  const s = loadSettings();
  return s.aiProvider !== 'none';
}

/**
 * Send a chat request to the configured AI provider via the API gateway proxy.
 *
 * @param messages   Conversation history (user/assistant turns).
 * @param system     Optional system prompt sent outside the message array.
 * @param maxTokens  Maximum tokens in the response (default 2048).
 * @param temperature  0 = deterministic, 0.2 = default (consistent), 1 = creative.
 *                     Use 0 for any call that must return valid JSON.
 */
export async function askAI(
  messages: AiMessage[],
  system?: string,
  maxTokens = 2048,
  temperature = 0.2,
): Promise<string> {
  const s = loadSettings();
  if (s.aiProvider === 'none') {
    throw new Error('No AI provider configured. Go to Settings → AI Assistant.');
  }
  const apiKey = s.aiProvider === 'claude' ? s.claudeApiKey : s.deepseekApiKey;
  const model = s.aiProvider === 'claude' ? s.claudeModel : s.deepseekModel;
  // Send whatever key we have (may be empty); backend falls back to Redis admin key.

  const base = process.env.NEXT_PUBLIC_API_URL ?? '/api';
  const token = typeof window !== 'undefined' ? localStorage.getItem('stockai_jwt')?.trim() : null;
  const res = await fetch(`${base}/ai/chat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({
      provider: s.aiProvider,
      model,
      api_key: apiKey,
      messages,
      system,
      max_tokens: maxTokens,
      temperature,
    }),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `AI request failed (${res.status})`);
  }
  const data = await res.json();
  return data.content as string;
}
