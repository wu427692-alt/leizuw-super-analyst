import { create } from 'zustand';
import { agentApi } from '../api/agent';
import type { ChatSessionItem, ChatStreamRequest } from '../api/agent';
import type { UserAccessStatus } from '../api/userAuth';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import { generateUUID } from '../utils/uuid';

// Persist only a session handle, never conversation text. Do not migrate the
// old unscoped handle between authenticated identities.
const sessionKey = (owner: string) => `dsa:chat:v2:${owner}:session`;
function readSession(owner: string | null): string | null {
  try { return owner ? localStorage.getItem(sessionKey(owner)) : null; }
  catch { return null; }
}
function saveSession(owner: string | null, session: string) {
  try { if (owner) localStorage.setItem(sessionKey(owner), session); }
  catch { /* Storage restrictions must not prevent in-memory chat. */ }
}
export function getChatOwnerScope(status: UserAccessStatus): string | null {
  if (!status.accessEnabled) return 'local';
  if (!status.loggedIn) return null;
  if (status.authMethod === 'admin') return 'admin';
  if (status.user?.status === 'approved' && status.user.id > 0) return `user:${status.user.id}`;
  return null;
}

export interface ProgressStep {
  type: string;
  step?: number;
  tool?: string;
  display_name?: string;
  success?: boolean;
  duration?: number;
  message?: string;
  content?: string;
}
export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  skills?: string[];
  skill?: string;
  skillNames?: string[];
  skillName?: string;
  thinkingSteps?: ProgressStep[];
}
export interface StreamMeta { skillNames?: string[]; skillName?: string }
type StreamEvent = ProgressStep & { session_id?: string; error?: unknown; background_continues?: boolean };

function streamFailure(event: StreamEvent, fallback: string): ParsedApiError {
  const meaningful = [event.error, event.message, event.content, fallback]
    .find(value => typeof value === 'string' ? value.trim() : value != null);
  return getParsedApiError(meaningful);
}
function connectionNotice(message: string): ParsedApiError {
  return { title: '回答尚未确认完成', message, rawMessage: message, category: 'unknown' };
}

interface AgentChatState {
  ownerScope: string | null;
  messages: Message[];
  messagesLoading: boolean;
  historyError: ParsedApiError | null;
  sessionsError: ParsedApiError | null;
  loading: boolean;
  progressSteps: ProgressStep[];
  sessionId: string;
  sessions: ChatSessionItem[];
  sessionsLoading: boolean;
  chatError: ParsedApiError | null;
  currentRoute: string;
  completionBadge: boolean;
  hasInitialLoad: boolean;
  abortController: AbortController | null;
}
interface AgentChatActions {
  setOwnerScope: (owner: string | null) => void;
  setCurrentRoute: (path: string) => void;
  clearCompletionBadge: () => void;
  loadSessions: () => Promise<void>;
  loadInitialSession: (preferredSessionId?: string) => Promise<void>;
  switchSession: (targetSessionId: string) => Promise<void>;
  retrySession: () => Promise<void>;
  stopStream: () => void;
  startNewChat: () => void;
  startStream: (payload: ChatStreamRequest, meta?: StreamMeta) => Promise<void>;
}

export const useAgentChatStore = create<AgentChatState & AgentChatActions>((set, get) => {
  let identityEpoch = 0;
  let historyRequest = 0;
  let listRequest = 0;
  let pendingQuestion: { message: Message; previousOccurrences: number } | null = null;

  const readHistory = async (target: string, preserve: boolean) => {
    const epoch = identityEpoch;
    const request = ++historyRequest;
    const current = () => identityEpoch === epoch && historyRequest === request && get().sessionId === target;
    set({ messagesLoading: true, historyError: null });
    try {
      const messages = await agentApi.getChatSessionMessages(target);
      if (!current()) return;
      // A disconnected request may not have saved anything yet. Keep the visible
      // question rather than presenting an empty conversation as a success.
      const savedOccurrences = pendingQuestion ? messages.filter(m => m.role === 'user' && m.content === pendingQuestion!.message.content).length : 0;
      if (preserve && pendingQuestion && savedOccurrences <= pendingQuestion.previousOccurrences) {
        set({ chatError: connectionNotice('服务端尚未确认保存本次问题，已保留当前内容。可稍后重新读取会话；不会自动重发问题。') });
      } else if (preserve && messages.length === 0 && get().messages.length > 0) {
        set({ chatError: connectionNotice('服务端暂未保存可恢复的内容。可稍后重新读取会话；不会自动重发问题。') });
      } else {
        const awaitingAnswer = messages.at(-1)?.role === 'user';
        if (!awaitingAnswer) pendingQuestion = null;
        set({ messages: messages.map(({ id, role, content }) => ({ id, role, content })),
          chatError: awaitingAnswer ? connectionNotice('已读取保存的问题，后台尚未保存对应答案。可稍后重新读取会话；不要重复提交同一分析。') : null });
      }
    } catch (error) {
      if (current()) set({ historyError: getParsedApiError(error) });
    } finally {
      if (current()) set({ messagesLoading: false });
    }
  };

  return {
    ownerScope: null,
    messages: [], messagesLoading: false, historyError: null, sessionsError: null,
    loading: false, progressSteps: [], sessionId: generateUUID(), sessions: [],
    sessionsLoading: false, chatError: null, currentRoute: '', completionBadge: false,
    hasInitialLoad: false, abortController: null,

    setOwnerScope: (ownerScope) => {
      if (get().ownerScope === ownerScope) return;
      identityEpoch++; historyRequest++; listRequest++;
      pendingQuestion = null;
      get().abortController?.abort();
      set({ ownerScope, sessionId: readSession(ownerScope) || generateUUID(), messages: [],
        messagesLoading: false, historyError: null, sessionsError: null,
        loading: false, progressSteps: [], sessions: [], sessionsLoading: false,
        chatError: null, completionBadge: false, hasInitialLoad: false, abortController: null });
    },
    setCurrentRoute: (currentRoute) => set({ currentRoute }),
    clearCompletionBadge: () => set({ completionBadge: false }),

    loadSessions: async () => {
      if (!get().ownerScope) return;
      const epoch = identityEpoch;
      const request = ++listRequest;
      const current = () => epoch === identityEpoch && request === listRequest;
      set({ sessionsLoading: true, sessionsError: null });
      try {
        const sessions = await agentApi.getChatSessions();
        if (current()) {
          const pending = get().loading && get().sessions.find(item => item.session_id === get().sessionId);
          set({ sessions: pending && !sessions.some(item => item.session_id === pending.session_id)
            ? [pending, ...sessions] : sessions });
        }
      } catch (error) {
        if (current()) set({ sessionsError: getParsedApiError(error) });
      } finally {
        if (current()) set({ sessionsLoading: false });
      }
    },

    loadInitialSession: async (preferredSessionId) => {
      if (!get().ownerScope) return;
      if (get().hasInitialLoad) {
        if (preferredSessionId && preferredSessionId !== get().sessionId) await get().switchSession(preferredSessionId);
        return;
      }
      set({ hasInitialLoad: true });
      const savedId = preferredSessionId || readSession(get().ownerScope);
      // List and detail are independent. Older sessions outside the first 50
      // can be restored; every detail request remains authorized by the API.
      await Promise.all([get().loadSessions(), savedId ? get().switchSession(savedId) : Promise.resolve()]);
    },

    switchSession: async (targetSessionId) => {
      if (!get().ownerScope) return;
      if (targetSessionId === get().sessionId && get().messages.length > 0 && !get().historyError) return;
      get().abortController?.abort();
      pendingQuestion = null;
      set({ sessionId: targetSessionId, messages: [], loading: false, progressSteps: [],
        chatError: null, abortController: null, hasInitialLoad: true });
      saveSession(get().ownerScope, targetSessionId);
      await readHistory(targetSessionId, false);
    },

    retrySession: async () => {
      if (!get().ownerScope || get().messagesLoading) return;
      get().abortController?.abort();
      set({ loading: false, abortController: null });
      await readHistory(get().sessionId, true);
    },

    stopStream: () => {
      if (!get().loading) return;
      get().abortController?.abort();
      set({ loading: false, abortController: null,
        chatError: connectionNotice('已停止等待，后台分析可能仍在继续。稍后可重新读取会话查看已保存的结果；此操作不会重新调用 AI。') });
    },

    startNewChat: () => {
      historyRequest++;
      pendingQuestion = null;
      get().abortController?.abort();
      const sessionId = generateUUID();
      set({ sessionId, messages: [], messagesLoading: false, historyError: null,
        loading: false, progressSteps: [], chatError: null, abortController: null, hasInitialLoad: true });
      saveSession(get().ownerScope, sessionId);
    },

    startStream: async (payload, meta) => {
      const state = get();
      if (!state.ownerScope || state.loading || state.messagesLoading || state.historyError || !payload.message.trim()) return;
      if (payload.session_id && payload.session_id !== state.sessionId) return;
      historyRequest++; listRequest++;
      state.abortController?.abort();
      const ac = new AbortController();
      const epoch = identityEpoch;
      const streamSessionId = state.sessionId;
      const current = () => epoch === identityEpoch && get().abortController === ac
        && get().sessionId === streamSessionId && !ac.signal.aborted;
      const skillNames = meta?.skillNames?.length ? meta.skillNames : [meta?.skillName ?? '通用'];
      const skills = { skills: payload.skills, skill: payload.skills?.[0], skillNames, skillName: skillNames.join('、') };
      const userMessage: Message = { id: generateUUID(), role: 'user', content: payload.message, ...skills };
      pendingQuestion = { message: userMessage, previousOccurrences: state.messages.filter(m => m.role === 'user' && m.content === payload.message).length };
      saveSession(state.ownerScope, streamSessionId);
      set(s => ({ abortController: ac, loading: true, progressSteps: [], chatError: null,
        hasInitialLoad: true, sessionsLoading: false,
        messages: [...s.messages, userMessage],
        sessions: s.sessions.some(item => item.session_id === streamSessionId) ? s.sessions : [{
          session_id: streamSessionId, title: payload.message.slice(0, 60), message_count: 1,
          created_at: new Date().toISOString(), last_active: new Date().toISOString(),
        }, ...s.sessions],
      }));

      let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
      let completed = false;
      const steps: ProgressStep[] = [];
      const cancelReader = () => { void reader?.cancel().catch(() => undefined); };
      try {
        const response = await agentApi.chatStream({ ...payload, session_id: streamSessionId }, { signal: ac.signal });
        if (!current()) { await response.body?.cancel(); return; }
        if (!response.body || !response.headers.get('content-type')?.includes('text/event-stream')) {
          throw connectionNotice('未收到有效的回答连接。可重新读取会话核对是否已保存结果，不会自动重复分析。');
        }
        reader = response.body.getReader();
        ac.signal.addEventListener('abort', cancelReader, { once: true });
        const decoder = new TextDecoder();
        let buffer = '';
        let eventLines: string[] = [];
        const dispatch = () => {
          if (!eventLines.length || !current() || completed) return;
          let event: StreamEvent;
          try { event = JSON.parse(eventLines.join('\n')); }
          catch { throw connectionNotice('回答连接返回了无法读取的内容。请重新读取会话核对结果，不会自动重发问题。'); }
          eventLines = [];
          if (!event || typeof event.type !== 'string' || (event.session_id && event.session_id !== streamSessionId)) {
            throw connectionNotice('收到的会话信息不匹配，已停止显示。请重新读取当前会话。');
          }
          if (event.type === 'error' || (event.type === 'done' && event.success === false)) {
            if (event.background_continues) throw connectionNotice(typeof event.message === 'string' ? event.message : '连接中断，后台可能继续。可重新读取会话查看结果。');
            throw streamFailure(event, event.type === 'error' ? '分析出错' : '大模型调用出错，请检查 API Key 配置');
          }
          if (event.type === 'done') {
            if (typeof event.content !== 'string' || !event.content.trim()) throw connectionNotice('AI 没有返回有效正文，未标记为完成。可重新读取会话或编辑问题后手动发送。');
            completed = true;
            pendingQuestion = null;
            set(s => ({ messages: [...s.messages, { id: generateUUID(), role: 'assistant', content: event.content!,
              ...skills, thinkingSteps: [...steps] }], completionBadge: s.currentRoute !== '/chat' }));
          } else {
            steps.push(event);
            set({ progressSteps: [...steps] });
          }
        };
        const consume = (text: string) => {
          buffer += text;
          let newline: number;
          while (!completed && (newline = buffer.indexOf('\n')) >= 0) {
            const line = buffer.slice(0, newline).replace(/\r$/, '');
            buffer = buffer.slice(newline + 1);
            if (!line) dispatch();
            else if (line.startsWith('data:')) eventLines.push(line.slice(5).replace(/^ /, ''));
            // SSE comments are keepalives, not business progress.
          }
        };
        while (current() && !completed) {
          const chunk = await reader.read();
          if (chunk.done) { consume(decoder.decode() + '\n\n'); break; }
          consume(decoder.decode(chunk.value, { stream: true }));
        }
        if (current() && !completed) throw connectionNotice('回答连接已中断，尚未收到完成结果；后台可能仍在分析。请重新读取会话查看已保存内容，不会自动重复调用 AI。');
      } catch (error) {
        if (current()) set({ chatError: getParsedApiError(error), completionBadge: get().currentRoute !== '/chat' });
      } finally {
        ac.signal.removeEventListener('abort', cancelReader);
        if (reader) { void reader.cancel().catch(() => undefined); reader.releaseLock(); }
        if (current()) {
          set({ loading: false, abortController: null, ...(completed ? { progressSteps: [] } : {}) });
          // Read-only refresh; never retry an AI POST after connection loss.
          void get().loadSessions();
        }
      }
    },
  };
});
