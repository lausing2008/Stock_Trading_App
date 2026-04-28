import { loadSettings } from './settings';

export type AiMessage = { role: 'user' | 'assistant'; content: string };

const PROVIDER_LABELS: Record<string, string> = {
  claude: 'Claude',
  deepseek: 'DeepSeek',
};

export function getAiProviderLabel(): string {
  return PROVIDER_LABELS[loadSettings().aiProvider] ?? 'AI';
}

export function isAiConfigured(): boolean {
  const s = loadSettings();
  if (s.aiProvider === 'none') return false;
  const key = s.aiProvider === 'claude' ? s.claudeApiKey : s.deepseekApiKey;
  return key.trim().length > 0;
}

export async function askAI(
  messages: AiMessage[],
  system?: string,
  maxTokens = 2048,
): Promise<string> {
  const s = loadSettings();
  if (s.aiProvider === 'none') {
    throw new Error('No AI provider configured. Go to Settings → AI Assistant.');
  }
  const apiKey = s.aiProvider === 'claude' ? s.claudeApiKey : s.deepseekApiKey;
  const model = s.aiProvider === 'claude' ? s.claudeModel : s.deepseekModel;
  if (!apiKey.trim()) {
    throw new Error(`${PROVIDER_LABELS[s.aiProvider]} API key not set. Go to Settings → AI Assistant.`);
  }

  const base = process.env.NEXT_PUBLIC_API_URL ?? '/api';
  const res = await fetch(`${base}/ai/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      provider: s.aiProvider,
      model,
      api_key: apiKey,
      messages,
      system,
      max_tokens: maxTokens,
    }),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `AI request failed (${res.status})`);
  }
  const data = await res.json();
  return data.content as string;
}
