import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useAgentChatStore } from '../agentChatStore';

vi.mock('../../api/agent', () => ({
  agentApi: {
    getChatSessions: vi.fn(async () => []),
    getChatSessionMessages: vi.fn(async () => []),
    chatStream: vi.fn(),
  },
}));

const { agentApi } = await import('../../api/agent');

const encoder = new TextEncoder();

function createStreamResponse(lines: string[]) {
  return new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(lines.join('\n\n')));
        controller.close();
      },
    }),
    {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    },
  );
}

function createDeferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
  vi.mocked(agentApi.getChatSessions).mockReset().mockResolvedValue([]);
  vi.mocked(agentApi.getChatSessionMessages).mockReset().mockResolvedValue([]);
  vi.mocked(agentApi.chatStream).mockReset();
  localStorage.clear();
  useAgentChatStore.getState().setOwnerScope(null);
  useAgentChatStore.getState().setOwnerScope('user:42');
  useAgentChatStore.setState({
    messages: [],
    loading: false,
    progressSteps: [],
    sessionId: 'session-test',
    sessions: [],
    sessionsLoading: false,
    chatError: null,
    currentRoute: '/chat',
    completionBadge: false,
    hasInitialLoad: true,
    abortController: null,
  });
});

describe('agentChatStore.startStream', () => {
  it('appends the user message and final assistant message from the SSE stream', async () => {
    vi.mocked(agentApi.chatStream).mockResolvedValue(
      createStreamResponse([
        'data: {"type":"thinking","step":1,"message":"分析中"}',
        'data: {"type":"tool_done","tool":"quote","display_name":"行情","success":true,"duration":0.3}',
        'data: {"type":"done","success":true,"content":"最终分析结果"}',
      ]),
    );

    await useAgentChatStore
      .getState()
      .startStream({ message: '分析茅台', session_id: 'session-test' }, { skillName: '趋势技能' });

    const state = useAgentChatStore.getState();
    expect(state.loading).toBe(false);
    expect(state.chatError).toBeNull();
    expect(state.messages).toHaveLength(2);
    expect(state.messages[0]).toMatchObject({
      role: 'user',
      content: '分析茅台',
      skillName: '趋势技能',
    });
    expect(state.messages[1]).toMatchObject({
      role: 'assistant',
      content: '最终分析结果',
      skillName: '趋势技能',
    });
    expect(state.messages[1].thinkingSteps).toHaveLength(2);
    expect(state.progressSteps).toEqual([]);
  });

  it('preserves multiple selected skills on streamed user and assistant messages', async () => {
    vi.mocked(agentApi.chatStream).mockResolvedValue(
      createStreamResponse([
        'data: {"type":"done","success":true,"content":"多策略分析结果"}',
      ]),
    );

    await useAgentChatStore
      .getState()
      .startStream(
        {
          message: '分析茅台',
          session_id: 'session-test',
          skills: ['bull_trend', 'ma_golden_cross'],
        },
        {
          skillNames: ['趋势分析', '均线金叉'],
        },
      );

    const state = useAgentChatStore.getState();
    expect(state.messages).toHaveLength(2);
    expect(state.messages[0]).toMatchObject({
      role: 'user',
      skills: ['bull_trend', 'ma_golden_cross'],
      skill: 'bull_trend',
      skillNames: ['趋势分析', '均线金叉'],
      skillName: '趋势分析、均线金叉',
    });
    expect(state.messages[1]).toMatchObject({
      role: 'assistant',
      content: '多策略分析结果',
      skills: ['bull_trend', 'ma_golden_cross'],
      skill: 'bull_trend',
      skillNames: ['趋势分析', '均线金叉'],
      skillName: '趋势分析、均线金叉',
    });
  });

  it('preserves parsed error details when done.success is false', async () => {
    vi.mocked(agentApi.chatStream).mockResolvedValue(
      createStreamResponse([
        'data: {"type":"done","success":false,"error":"Agent LLM: no effective primary model configured"}',
      ]),
    );

    await useAgentChatStore
      .getState()
      .startStream({ message: '分析茅台', session_id: 'session-test' }, { skillName: '趋势技能' });

    const state = useAgentChatStore.getState();
    expect(state.loading).toBe(false);
    expect(state.messages).toHaveLength(1);
    expect(state.chatError).toMatchObject({
      title: '系统没有配置可用的 LLM 模型',
      message: '请先在系统设置中配置主模型、可用渠道或相关 API Key 后再重试。',
      category: 'llm_not_configured',
      rawMessage: 'Agent LLM: no effective primary model configured',
    });
  });

  it('uses the same parser for SSE error events', async () => {
    vi.mocked(agentApi.chatStream).mockResolvedValue(
      createStreamResponse([
        'data: {"type":"error","message":"connect timeout while calling upstream provider"}',
      ]),
    );

    await useAgentChatStore
      .getState()
      .startStream({ message: '分析茅台', session_id: 'session-test' }, { skillName: '趋势技能' });

    const state = useAgentChatStore.getState();
    expect(state.loading).toBe(false);
    expect(state.messages).toHaveLength(1);
    expect(state.chatError).toMatchObject({
      title: '连接上游服务超时',
      message: '服务端访问外部依赖时超时，请稍后重试，或检查当前网络与代理设置。',
      category: 'upstream_timeout',
      rawMessage: 'connect timeout while calling upstream provider',
    });
  });

  it('falls back when SSE error fields are empty strings', async () => {
    vi.mocked(agentApi.chatStream).mockResolvedValue(
      createStreamResponse([
        'data: {"type":"error","error":"","message":"   ","content":""}',
      ]),
    );

    await useAgentChatStore
      .getState()
      .startStream({ message: '分析茅台', session_id: 'session-test' }, { skillName: '趋势技能' });

    const state = useAgentChatStore.getState();
    expect(state.loading).toBe(false);
    expect(state.messages).toHaveLength(1);
    expect(state.chatError).toMatchObject({
      title: '请求失败',
      message: '分析出错',
      category: 'unknown',
      rawMessage: '分析出错',
    });
  });
});

describe('agentChatStore.switchSession', () => {

  it('clears transient loading state when switching sessions during a stream', async () => {
    const ac = new AbortController();
    vi.mocked(agentApi.getChatSessionMessages).mockResolvedValue([
      { id: 'msg-2', role: 'assistant', content: '历史回复', created_at: null },
    ]);
    useAgentChatStore.setState({
      loading: true,
      progressSteps: [{ type: 'thinking', message: '正在制定分析路径...' }],
      abortController: ac,
      chatError: {
        title: '请求失败',
        message: '旧错误',
        category: 'unknown',
        rawMessage: '旧错误',
      },
    });

    await useAgentChatStore.getState().switchSession('session-2');

    const state = useAgentChatStore.getState();
    expect(ac.signal.aborted).toBe(true);
    expect(state.sessionId).toBe('session-2');
    expect(state.loading).toBe(false);
    expect(state.progressSteps).toEqual([]);
    expect(state.abortController).toBeNull();
    expect(state.chatError).toBeNull();
    expect(state.messages).toEqual([
      { id: 'msg-2', role: 'assistant', content: '历史回复' },
    ]);
  });

  it('does not let a late session history response overwrite the current session', async () => {
    const sessionA = createDeferred<
      Array<{ id: string; role: 'user' | 'assistant'; content: string; created_at: string | null }>
    >();
    const sessionB = createDeferred<
      Array<{ id: string; role: 'user' | 'assistant'; content: string; created_at: string | null }>
    >();
    vi.mocked(agentApi.getChatSessionMessages).mockImplementation((targetSessionId: string) => {
      if (targetSessionId === 'session-a') return sessionA.promise;
      if (targetSessionId === 'session-b') return sessionB.promise;
      return Promise.resolve([]);
    });

    const switchToA = useAgentChatStore.getState().switchSession('session-a');
    const switchToB = useAgentChatStore.getState().switchSession('session-b');

    sessionB.resolve([{ id: 'msg-b', role: 'assistant', content: 'B 回复', created_at: null }]);
    await switchToB;

    sessionA.resolve([{ id: 'msg-a', role: 'assistant', content: 'A 回复', created_at: null }]);
    await switchToA;

    const state = useAgentChatStore.getState();
    expect(state.sessionId).toBe('session-b');
    expect(state.messages).toEqual([
      { id: 'msg-b', role: 'assistant', content: 'B 回复' },
    ]);
  });
});

describe('chat lifecycle boundaries', () => {
  it.each([
    ['premature EOF', ['data: {"type":"thinking","message":"核验中"}']],
    ['empty answer', ['data: {"type":"done","success":true,"content":" "}']],
    ['malformed event', ['data: invalid-json']],
    ['foreign session', ['data: {"type":"done","session_id":"other","content":"private"}']],
  ])('never manufactures a completed answer after %s', async (_name, lines) => {
    vi.mocked(agentApi.chatStream).mockResolvedValue(createStreamResponse(lines));
    await useAgentChatStore.getState().startStream({ message: '问题' });
    const state = useAgentChatStore.getState();
    expect(state.messages.map(m => m.content)).toEqual(['问题']);
    expect(state.chatError).not.toBeNull();
    expect(state.loading).toBe(false);
    expect(agentApi.chatStream).toHaveBeenCalledTimes(1);
  });

  it('keeps progress on disconnection and recovers using only a history GET', async () => {
    vi.mocked(agentApi.chatStream).mockResolvedValue(createStreamResponse(['data: {"type":"thinking","message":"核验中"}']));
    await useAgentChatStore.getState().startStream({ message: '问题' });
    expect(useAgentChatStore.getState().progressSteps).toHaveLength(1);
    vi.mocked(agentApi.getChatSessionMessages).mockResolvedValue([
      { id: '1', role: 'user', content: '问题', created_at: null },
      { id: '2', role: 'assistant', content: '后台已保存的答案', created_at: null },
    ]);
    await useAgentChatStore.getState().retrySession();
    expect(useAgentChatStore.getState().messages.at(-1)?.content).toBe('后台已保存的答案');
    expect(useAgentChatStore.getState().chatError).toBeNull();
    expect(agentApi.chatStream).toHaveBeenCalledTimes(1);
  });

  it('retains a new unsaved question when readonly recovery returns nonempty old history', async () => {
    useAgentChatStore.setState({ messages: [{ id: 'old1', role: 'user', content: '旧问题' }, { id: 'old2', role: 'assistant', content: '旧答案' }] });
    vi.mocked(agentApi.chatStream).mockResolvedValue(createStreamResponse([]));
    await useAgentChatStore.getState().startStream({ message: '本次新问题' });
    vi.mocked(agentApi.getChatSessionMessages).mockResolvedValue([
      { id: 'old1', role: 'user', content: '旧问题', created_at: null },
      { id: 'old2', role: 'assistant', content: '旧答案', created_at: null },
    ]);
    await useAgentChatStore.getState().retrySession();
    expect(useAgentChatStore.getState().messages.at(-1)?.content).toBe('本次新问题');
    expect(useAgentChatStore.getState().chatError?.message).toContain('尚未确认保存本次问题');
    expect(agentApi.chatStream).toHaveBeenCalledTimes(1);
  });

  it('keeps readonly recovery available until a saved question receives its answer', async () => {
    vi.mocked(agentApi.chatStream).mockResolvedValue(createStreamResponse([]));
    await useAgentChatStore.getState().startStream({ message: '问题' });
    vi.mocked(agentApi.getChatSessionMessages).mockResolvedValue([{ id: 'q', role: 'user', content: '问题', created_at: null }]);
    await useAgentChatStore.getState().retrySession();
    expect(useAgentChatStore.getState().chatError?.message).toContain('尚未保存对应答案');
    expect(useAgentChatStore.getState().messages.at(-1)?.role).toBe('user');
  });

  it('handles split UTF8, comments, multiline SSE and completes before server closes', async () => {
    const bytes = encoder.encode(': keepalive\r\n\r\ndata:{"type":"done",\r\ndata:"content":"中文结果"}\r\n\r\n');
    const cancel = vi.fn();
    vi.mocked(agentApi.chatStream).mockResolvedValue(new Response(new ReadableStream({
      start(controller) { for (const byte of bytes) controller.enqueue(new Uint8Array([byte])); }, cancel,
    }), { headers: { 'content-type': 'text/event-stream' } }));
    await useAgentChatStore.getState().startStream({ message: '问题' });
    expect(useAgentChatStore.getState().messages.at(-1)?.content).toBe('中文结果');
    expect(useAgentChatStore.getState().messages.at(-1)?.thinkingSteps).toEqual([]);
    expect(cancel).toHaveBeenCalled();
  });

  it('rejects an HTML gateway page instead of reporting empty success', async () => {
    vi.mocked(agentApi.chatStream).mockResolvedValue(new Response('<html>gateway</html>', { headers: { 'content-type': 'text/html' } }));
    await useAgentChatStore.getState().startStream({ message: '问题' });
    expect(useAgentChatStore.getState().chatError?.message).toContain('有效的回答连接');
    expect(useAgentChatStore.getState().messages).toHaveLength(1);
  });

  it('suppresses late failure from an aborted request after starting a new chat', async () => {
    const pending = createDeferred<Response>();
    vi.mocked(agentApi.chatStream).mockReturnValue(pending.promise);
    const stream = useAgentChatStore.getState().startStream({ message: '旧问题' });
    useAgentChatStore.getState().startNewChat();
    pending.reject(new Error('late old error'));
    await stream;
    expect(useAgentChatStore.getState().chatError).toBeNull();
    expect(useAgentChatStore.getState().messages).toEqual([]);
    expect(agentApi.getChatSessions).not.toHaveBeenCalled();
  });

  it('stops waiting, retains the question, and never promises AI cancellation', async () => {
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    vi.mocked(agentApi.chatStream).mockResolvedValue(new Response(new ReadableStream({ start(c) { controller = c; } }),
      { headers: { 'content-type': 'text/event-stream' } }));
    const stream = useAgentChatStore.getState().startStream({ message: '问题' });
    await Promise.resolve();
    controller.enqueue(encoder.encode('data: {"type":"thinking","message":"处理中"}\n\n'));
    await Promise.resolve();
    useAgentChatStore.getState().stopStream();
    await stream;
    expect(useAgentChatStore.getState().messages[0].content).toBe('问题');
    expect(useAgentChatStore.getState().chatError?.message).toContain('后台分析可能仍在继续');
    expect(useAgentChatStore.getState().loading).toBe(false);
  });

  it('restores an old bookmarked session even if it is not in the first 50', async () => {
    useAgentChatStore.setState({ hasInitialLoad: false });
    vi.mocked(agentApi.getChatSessionMessages).mockResolvedValue([{ id: 'old', role: 'assistant', content: '历史', created_at: null }]);
    await useAgentChatStore.getState().loadInitialSession('old-session');
    expect(agentApi.getChatSessionMessages).toHaveBeenCalledWith('old-session');
    expect(useAgentChatStore.getState().messages[0].content).toBe('历史');
  });

  it('does not let a late initial history overwrite a newly created chat', async () => {
    const pending = createDeferred<[]>();
    vi.mocked(agentApi.getChatSessionMessages).mockReturnValue(pending.promise);
    useAgentChatStore.setState({ hasInitialLoad: false });
    const initial = useAgentChatStore.getState().loadInitialSession('old');
    useAgentChatStore.getState().startNewChat();
    const newId = useAgentChatStore.getState().sessionId;
    pending.resolve([]); await initial;
    expect(useAgentChatStore.getState().sessionId).toBe(newId);
    expect(useAgentChatStore.getState().messagesLoading).toBe(false);
  });

  it('retains old sessions after list failure and permits explicit retry', async () => {
    useAgentChatStore.setState({ sessions: [{ session_id: 'old', title: '已有历史', message_count: 2, created_at: null, last_active: null }] });
    vi.mocked(agentApi.getChatSessions).mockRejectedValueOnce(new Error('offline'));
    await useAgentChatStore.getState().loadSessions();
    expect(useAgentChatStore.getState().sessions[0].title).toBe('已有历史');
    expect(useAgentChatStore.getState().sessionsError).not.toBeNull();
    await useAgentChatStore.getState().loadSessions();
    expect(useAgentChatStore.getState().sessionsError).toBeNull();
  });

  it('blocks sending while history is unresolved and permits readonly retry after failure', async () => {
    const pending = createDeferred<[]>();
    vi.mocked(agentApi.getChatSessionMessages).mockReturnValueOnce(pending.promise);
    const switching = useAgentChatStore.getState().switchSession('saved');
    await useAgentChatStore.getState().startStream({ message: 'must not send' });
    expect(agentApi.chatStream).not.toHaveBeenCalled();
    pending.reject(new Error('offline')); await switching;
    expect(useAgentChatStore.getState().historyError).not.toBeNull();
    await useAgentChatStore.getState().retrySession();
    expect(useAgentChatStore.getState().historyError).toBeNull();
    expect(useAgentChatStore.getState().messagesLoading).toBe(false);
  });

  it('invalidates old identity history even when the next user opens the same handle', async () => {
    const pending = createDeferred<[{ id: string; role: 'assistant'; content: string; created_at: null }]>();
    vi.mocked(agentApi.getChatSessionMessages).mockReturnValueOnce(pending.promise);
    const oldRead = useAgentChatStore.getState().switchSession('shared-handle');
    useAgentChatStore.getState().setOwnerScope('user:43');
    await useAgentChatStore.getState().switchSession('shared-handle');
    pending.resolve([{ id: 'old', role: 'assistant', content: 'other user private answer', created_at: null }]);
    await oldRead;
    expect(useAgentChatStore.getState().messages).toEqual([]);
  });

  it('does not crash when browser storage is unavailable', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new DOMException('blocked', 'SecurityError'); });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('quota', 'QuotaExceededError'); });
    expect(() => useAgentChatStore.getState().setOwnerScope('user:99')).not.toThrow();
    expect(() => useAgentChatStore.getState().startNewChat()).not.toThrow();
    expect(useAgentChatStore.getState().sessionId).toBeTruthy();
  });
});
