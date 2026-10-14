/**
 * Evidence-linked copilot client (research-engine `/copilot/*`).
 *
 * Unlike `askAI()`, the browser sends only the QUESTION. The server builds the evidence from
 * its own tables, checks that every figure in the answer came from that evidence, and returns
 * the citations, the data's as-of time and its limitations alongside the answer.
 *
 * `request_id` is the idempotency key: retrying with the same id returns the stored answer
 * instead of paying for a second model call. Generate a new one only for a NEW question.
 */
import { loadSettings } from './settings';

export interface CopilotEvidence {
  id: string; source?: string | null; observed_period?: string | null;
  published_at?: string | null; note?: string | null;
}
export interface CopilotEvidenceRef {
  key: string; label: string; display: string; state: string; note?: string | null;
  url?: string | null; evidence: CopilotEvidence[];
}
export interface CopilotMessage {
  id?: number; role: 'user' | 'assistant'; content: string; request_id: string;
  status?: 'grounded' | 'rejected' | null; created_at?: string | null;
  evidence_refs?: CopilotEvidenceRef[]; limitations?: string[]; data_as_of?: string | null;
  rejection_reasons?: string[]; model?: string | null;
}
export interface CopilotConversation {
  id: number; subject_key: string; title: string; created_at?: string; updated_at?: string;
}

const BASE = process.env.NEXT_PUBLIC_API_URL ?? '/api';
/** Above the server's two-attempt worst case, below the gateway's 240 s. */
const TIMEOUT_MS = 200_000;

function headers(): Record<string, string> {
  const token = typeof window !== 'undefined' ? localStorage.getItem('stockai_jwt')?.trim() : null;
  return { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) };
}

async function call<T>(path: string, init: RequestInit = {}, signal?: AbortSignal): Promise<T> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS);
  signal?.addEventListener('abort', () => ctl.abort());
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, { ...init, headers: headers(), signal: ctl.signal });
  } catch (e) {
    if (e instanceof Error && e.name === 'AbortError') {
      throw new Error(signal?.aborted ? 'Cancelled' : 'The copilot took too long to answer');
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error((err as { detail?: string }).detail || `Copilot request failed (${res.status})`);
  }
  return (await res.json()) as T;
}

export function newRequestId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID().replace(/-/g, '');
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
}

export async function askCopilot(
  args: { symbol: string; question: string; requestId: string; conversationId?: number | null },
  signal?: AbortSignal,
): Promise<{ conversation_id: number; message: CopilotMessage; replayed: boolean }> {
  const s = loadSettings();
  const provider = s.aiProvider === 'deepseek' ? 'deepseek' : 'claude';
  return call('/copilot/chat', {
    method: 'POST',
    body: JSON.stringify({
      symbol: args.symbol, question: args.question, request_id: args.requestId,
      conversation_id: args.conversationId ?? null, provider,
      model: provider === 'claude' ? s.claudeModel : s.deepseekModel,
      api_key: provider === 'claude' ? s.claudeApiKey : s.deepseekApiKey,
    }),
  }, signal);
}

export function listCopilotConversations(symbol: string) {
  return call<{ conversations: CopilotConversation[]; retention_days: number }>(
    `/copilot/conversations?symbol=${encodeURIComponent(symbol)}&limit=5`);
}

export function getCopilotConversation(id: number) {
  return call<{ conversation: CopilotConversation; messages: CopilotMessage[]; retention_days: number }>(
    `/copilot/conversations/${id}`);
}

export function deleteCopilotConversation(id: number) {
  return call<{ deleted: number }>(`/copilot/conversations/${id}`, { method: 'DELETE' });
}
