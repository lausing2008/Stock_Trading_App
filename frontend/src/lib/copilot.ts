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
  published_at?: string | null; note?: string | null; value?: unknown;
}
export interface CopilotEvidenceRef {
  key: string; label: string; display: string; state: string; note?: string | null;
  url?: string | null; evidence: CopilotEvidence[];
  /** Total records behind this citation; `evidence` is a preview of the first few. */
  evidence_count?: number;
}
export interface CopilotDraft {
  id: string; kind: 'price_alert' | 'watchlist_add' | 'signal_alert';
  arguments: Record<string, string | number>;
  summary: string;
  threshold_source?: { kind: 'packet' | 'typed'; key: string | null; display: string; state?: string };
  side_effects: string[];
  expires_at: string;
  status: 'proposed' | 'saved';
  result?: Record<string, unknown>;
}
export interface CopilotMessage {
  id?: number; role: 'user' | 'assistant'; content: string; request_id: string;
  status?: 'grounded' | 'rejected' | null; created_at?: string | null;
  evidence_refs?: CopilotEvidenceRef[]; limitations?: string[];
  /** Time of the latest daily PRICE bar — other sources carry their own times. */
  price_as_of?: string | null;
  source_times?: { price?: string | null; signal?: string | null; report_generated?: string | null; newest_headline?: string | null };
  rejection_reasons?: string[]; model?: string | null;
  /** Actions proposed for the user to review and save. Nothing is saved until they confirm. */
  drafts?: CopilotDraft[];
  dropped_drafts?: { kind: string; reason: string }[];
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

/**
 * Stop any FURTHER model call for this question. A call already running still completes and
 * is counted; its answer is stored and appears when the conversation is reopened.
 */
export function cancelCopilot(requestId: string) {
  return call<{ cancelled: boolean; note: string }>('/copilot/cancel', {
    method: 'POST', body: JSON.stringify({ request_id: requestId }),
  });
}

/** Save a reviewed draft through the platform's normal endpoint, with your credentials. */
export function confirmCopilotDraft(messageId: number, draftId: string) {
  return call<{ draft: CopilotDraft; replayed: boolean }>('/copilot/drafts/confirm', {
    method: 'POST', body: JSON.stringify({ message_id: messageId, draft_id: draftId }),
  });
}

/** Every evidence record behind one citation of one of your answers. */
export function getCopilotEvidence(messageId: number, key: string) {
  return call<{ message_id: number; key: string; label: string; display: string; evidence: CopilotEvidence[] }>(
    `/copilot/messages/${messageId}/evidence?key=${encodeURIComponent(key)}`);
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

export interface CopilotScreenResult {
  question: string;
  status: 'ok' | 'empty_scope' | 'nothing_applied' | 'not_understood';
  message?: string;
  scope: { kind: 'watchlist' | 'all_ranked'; description: string; requested?: number | null };
  interpreted: { filter: string; value: string | number; label: string }[];
  dropped: { filter: string; value: string; reason: string }[];
  unsupported: string[];
  sort_by?: string; limit?: number;
  coverage: { universe: number | null; matched: number | null; shown: number; unranked: string[]; window_days?: number };
  results: { symbol: string; name?: string; sector?: string | null; market?: string; score?: number | null;
    momentum?: number | null; technical?: number | null; rs_score?: number | null; signal?: string | null;
    confidence?: number | null }[];
  basis?: string;
}

/** Natural-language screen in YOUR scope (admins: all ranked stocks; others: own watchlists). */
export async function screenCopilot(question: string, requestId: string, signal?: AbortSignal) {
  const s = loadSettings();
  const provider = s.aiProvider === 'deepseek' ? 'deepseek' : 'claude';
  return call<CopilotScreenResult>('/copilot/screen', {
    method: 'POST',
    body: JSON.stringify({
      question, request_id: requestId, provider,
      model: provider === 'claude' ? s.claudeModel : s.deepseekModel,
      api_key: provider === 'claude' ? s.claudeApiKey : s.deepseekApiKey,
    }),
  }, signal);
}
