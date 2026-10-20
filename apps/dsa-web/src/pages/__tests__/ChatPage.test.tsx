import { StrictMode } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { createMemoryRouter, MemoryRouter, RouterProvider } from 'react-router-dom';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { createParsedApiError, type ParsedApiError } from '../../api/error';
import { historyApi } from '../../api/history';
import type { Message, ProgressStep } from '../../stores/agentChatStore';
import ChatPage from '../ChatPage';
import { extractStockCodeFromMessage, extractStockCodesFromMessage } from '../../utils/chatStockCode';

function createDeferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const {
  mockGetSkills,
  mockDeleteChatSession,
  mockSendChat,
  mockGetSystemConfig,
  mockUpdateSystemConfig,
  mockGetWatchlist,
  mockAddToWatchlist,
  mockRemoveFromWatchlist,
  mockDownloadSession,
  mockFormatSessionAsMarkdown,
} = vi.hoisted(() => ({
  mockGetSkills: vi.fn(),
  mockDeleteChatSession: vi.fn(),
  mockSendChat: vi.fn(),
  mockGetSystemConfig: vi.fn(),
  mockUpdateSystemConfig: vi.fn(),
  mockGetWatchlist: vi.fn(),
  mockAddToWatchlist: vi.fn(),
  mockRemoveFromWatchlist: vi.fn(),
  mockDownloadSession: vi.fn(),
  mockFormatSessionAsMarkdown: vi.fn(),
}));

const mockLoadSessions = vi.fn();
const mockLoadInitialSession = vi.fn();
const mockSwitchSession = vi.fn();
const mockStartStream = vi.fn();
const mockClearCompletionBadge = vi.fn();
const mockStartNewChat = vi.fn();
const mockRetrySession = vi.fn();
const mockStopStream = vi.fn();
const mockAccessState = { accessEnabled: false, loggedIn: true, user: { id: 1, name: '测试用户', status: 'approved' }, authMethod: 'local' };

vi.mock('../../contexts/UserAccessContext', () => ({ useUserAccess: () => mockAccessState }));

const mockStoreState = {
  messages: [] as Message[],
  loading: false,
  messagesLoading: false,
  historyError: null as ParsedApiError | null,
  sessionsError: null as ParsedApiError | null,
  progressSteps: [] as ProgressStep[],
  sessionId: 'session-1',
  sessions: [
    {
      session_id: 'session-1',
      title: '请简要分析 600519',
      message_count: 2,
      created_at: '2026-03-15T09:00:00Z',
      last_active: '2026-03-15T09:05:00Z',
    },
  ],
  sessionsLoading: false,
  chatError: null as ParsedApiError | null,
  loadSessions: mockLoadSessions,
  loadInitialSession: mockLoadInitialSession,
  switchSession: mockSwitchSession,
  startStream: mockStartStream,
  clearCompletionBadge: mockClearCompletionBadge,
  retrySession: mockRetrySession,
  stopStream: mockStopStream,
};

vi.mock('../../api/agent', () => ({
  agentApi: {
    getSkills: mockGetSkills,
    deleteChatSession: mockDeleteChatSession,
    sendChat: mockSendChat,
  },
}));

vi.mock('../../api/systemConfig', () => ({
  systemConfigApi: {
    getConfig: mockGetSystemConfig,
    update: mockUpdateSystemConfig,
    getWatchlist: mockGetWatchlist,
    addToWatchlist: mockAddToWatchlist,
    removeFromWatchlist: mockRemoveFromWatchlist,
  },
}));

vi.mock('../../utils/chatExport', () => ({
  downloadSession: mockDownloadSession,
  formatSessionAsMarkdown: mockFormatSessionAsMarkdown,
}));

vi.mock('../../api/history', () => ({
  historyApi: {
    getDetail: vi.fn().mockResolvedValue({}),
  },
}));

vi.mock('../../stores/agentChatStore', () => {
  const useAgentChatStore = (
    selector?: (state: typeof mockStoreState) => unknown
  ) => (typeof selector === 'function' ? selector(mockStoreState) : mockStoreState);

  useAgentChatStore.getState = () => ({
    ...mockStoreState,
    startNewChat: mockStartNewChat,
  });

  return { useAgentChatStore };
});

beforeAll(() => {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: query === '(prefers-color-scheme: dark)',
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });

  Object.defineProperty(window, 'requestAnimationFrame', {
    writable: true,
    value: (callback: FrameRequestCallback) => window.setTimeout(() => callback(0), 0),
  });

  Object.defineProperty(window, 'cancelAnimationFrame', {
    writable: true,
    value: (handle: number) => window.clearTimeout(handle),
  });

  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
    writable: true,
    value: vi.fn(),
  });
});

beforeEach(() => {
  vi.clearAllMocks();
  mockStoreState.messages = [];
  mockStoreState.loading = false;
  mockStoreState.messagesLoading = false;
  mockStoreState.historyError = null;
  mockStoreState.sessionsError = null;
  Object.assign(mockAccessState, { accessEnabled: false, loggedIn: true, user: { id: 1, name: '测试用户', status: 'approved' }, authMethod: 'local' });
  mockStoreState.progressSteps = [];
  mockStoreState.chatError = null;
  mockStoreState.sessionsLoading = false;
  mockStoreState.sessionId = 'session-1';
  mockStoreState.sessions = [
    {
      session_id: 'session-1',
      title: '请简要分析 600519',
      message_count: 2,
      created_at: '2026-03-15T09:00:00Z',
      last_active: '2026-03-15T09:05:00Z',
    },
  ];
  mockGetSkills.mockResolvedValue({
    skills: [
      { id: 'bull_trend', name: '趋势分析', description: '测试技能' },
    ],
    default_skill_id: 'bull_trend',
  });
  mockDeleteChatSession.mockResolvedValue(undefined);
  mockSendChat.mockResolvedValue({ success: true });
  mockGetWatchlist.mockResolvedValue([]);
  mockGetSystemConfig.mockResolvedValue({
    configVersion: 'cfg-v1',
    maskToken: 'mask-token',
    items: [
      {
        key: 'AGENT_CONTEXT_COMPRESSION_ENABLED',
        value: 'false',
        rawValueExists: true,
        isMasked: false,
      },
    ],
  });
  mockUpdateSystemConfig.mockResolvedValue({
    success: true,
    configVersion: 'cfg-v2',
    appliedCount: 1,
    skippedMaskedCount: 0,
    reloadTriggered: true,
    updatedKeys: ['AGENT_CONTEXT_COMPRESSION_ENABLED'],
    warnings: [],
  });
  mockDownloadSession.mockImplementation(() => {});
  mockFormatSessionAsMarkdown.mockReturnValue('# exported session');
});

describe('ChatPage recovery and identity boundaries', () => {
  const temporaryError = () => createParsedApiError({ title: '连接中断', message: '请稍后重试', category: 'unknown', rawMessage: '请稍后重试' });

  it('distinguishes history loading from an empty conversation and blocks sends', async () => {
    mockStoreState.messagesLoading = true;
    render(<MemoryRouter><ChatPage /></MemoryRouter>);
    expect(await screen.findByText('正在读取历史会话…')).toBeInTheDocument();
    expect(screen.queryByText('开始问股')).not.toBeInTheDocument();
    const input = screen.getByRole('textbox');
    expect(input).toBeDisabled();
    fireEvent.change(input, { target: { value: '不要重复提交' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(mockStartStream).not.toHaveBeenCalled();
  });

  it('retains loaded history on a read error and retries only the read', async () => {
    mockStoreState.messages = [{ id: 'old', role: 'assistant', content: '之前保存的分析' }];
    mockStoreState.historyError = temporaryError();
    render(<MemoryRouter><ChatPage /></MemoryRouter>);
    expect(await screen.findByText('之前保存的分析')).toBeInTheDocument();
    expect(screen.getByRole('textbox')).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '重新读取历史会话' }));
    expect(mockRetrySession).toHaveBeenCalledOnce();
    expect(mockStartStream).not.toHaveBeenCalled();
  });

  it('keeps session rows when refreshing fails and does not mislabel failed reads as empty', async () => {
    mockStoreState.sessionsError = temporaryError();
    const view = render(<MemoryRouter><ChatPage /></MemoryRouter>);
    expect(await screen.findByRole('button', { name: '切换到对话 请简要分析 600519' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试对话列表' }));
    expect(mockLoadSessions).toHaveBeenCalledOnce();
    mockStoreState.sessions = [];
    view.rerender(<MemoryRouter><ChatPage /></MemoryRouter>);
    expect(screen.queryByText('暂无历史对话')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重试对话列表' })).toBeInTheDocument();
  });

  it('offers read-only recovery and edit instead of resubmitting a failed question', async () => {
    mockStoreState.messages = [{ id: 'question', role: 'user', content: '华懋科技的利润增长来自哪里' }];
    mockStoreState.chatError = temporaryError();
    mockStoreState.progressSteps = [{ type: 'tool_done', tool: 'quote', display_name: '行情证据', success: true, duration: 1 }];
    render(<MemoryRouter><ChatPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: '重新读取会话' }));
    fireEvent.click(screen.getByRole('button', { name: '重新编辑上次问题' }));
    expect(mockRetrySession).toHaveBeenCalledOnce();
    expect(screen.getByRole('textbox')).toHaveValue('华懋科技的利润增长来自哪里');
    fireEvent.click(screen.getByText('查看断开前的处理进度'));
    expect(screen.getByText('行情证据 (1s)')).toBeInTheDocument();
    expect(mockStartStream).not.toHaveBeenCalled();
  });

  it('stops waiting without claiming backend cancellation and can read the eventual result', async () => {
    mockStoreState.loading = true;
    const view = render(<MemoryRouter><ChatPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: '停止等待' }));
    expect(mockStopStream).toHaveBeenCalledOnce();
    mockStoreState.loading = false;
    mockStoreState.chatError = createParsedApiError({ title: '回答尚未确认完成', message: '已停止等待，后台分析可能仍在继续。', rawMessage: '已停止等待，后台分析可能仍在继续。', category: 'unknown' });
    view.rerender(<MemoryRouter><ChatPage /></MemoryRouter>);
    expect(screen.getByText(/已停止等待，后台分析可能仍在继续/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重新读取会话' }));
    expect(mockRetrySession).toHaveBeenCalledOnce();
    expect(mockStartStream).not.toHaveBeenCalled();
  });

  it('does not send Chinese IME confirmation Enter or keyCode 229', async () => {
    render(<MemoryRouter><ChatPage /></MemoryRouter>);
    const input = await screen.findByRole('textbox');
    fireEvent.change(input, { target: { value: '华懋科技' } });
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true });
    fireEvent.keyDown(input, { key: 'Enter', keyCode: 229 });
    expect(mockStartStream).not.toHaveBeenCalled();
    expect(input).toHaveValue('华懋科技');
    fireEvent.keyDown(input, { key: 'Enter', isComposing: false });
    expect(mockStartStream).toHaveBeenCalledOnce();
  });

  it('updates asynchronously loaded watchlist state in StrictMode', async () => {
    const watchlist = createDeferred<string[]>();
    mockGetWatchlist.mockReturnValue(watchlist.promise);
    render(<StrictMode><MemoryRouter initialEntries={['/chat?stock=600519']}><ChatPage /></MemoryRouter></StrictMode>);
    await act(async () => { watchlist.resolve(['600519']); });
    expect(await screen.findByRole('button', { name: '从自选删除' })).toBeInTheDocument();
  });

  it('shows deletion failure without clearing the current conversation', async () => {
    mockDeleteChatSession.mockRejectedValueOnce(new Error('删除服务暂不可用'));
    render(<MemoryRouter><ChatPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: '删除对话 请简要分析 600519' }));
    fireEvent.click(screen.getByRole('button', { name: '删除' }));
    expect(await screen.findByText('对话未删除')).toBeInTheDocument();
    expect(screen.getByText('删除服务暂不可用')).toBeInTheDocument();
    expect(mockStartNewChat).not.toHaveBeenCalled();
    expect(mockSwitchSession).not.toHaveBeenCalled();
  });

  it('does not clear another conversation when deletion finishes after switching', async () => {
    const deletion = createDeferred<void>();
    mockDeleteChatSession.mockReturnValueOnce(deletion.promise);
    render(<MemoryRouter><ChatPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: '删除对话 请简要分析 600519' }));
    fireEvent.click(screen.getByRole('button', { name: '删除' }));
    mockStoreState.sessionId = 'session-other';
    await act(async () => { deletion.resolve(); });
    expect(mockLoadSessions).toHaveBeenCalledOnce();
    expect(mockStartNewChat).not.toHaveBeenCalled();
  });

  it('allows general analysis when skills fail and offers an explicit retry', async () => {
    mockGetSkills.mockRejectedValueOnce(new Error('策略暂不可用'));
    render(<MemoryRouter><ChatPage /></MemoryRouter>);
    expect(await screen.findByText(/可直接使用通用分析/)).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '分析华懋科技' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));
    expect(mockStartStream).toHaveBeenCalledWith(expect.not.objectContaining({ skills: expect.anything() }), expect.anything());
    fireEvent.click(screen.getByRole('button', { name: '重新加载策略' }));
    expect(await screen.findByRole('checkbox', { name: '趋势分析' })).toBeChecked();
  });

  it('does not request global settings or expose shared notification actions to ordinary users', async () => {
    Object.assign(mockAccessState, { accessEnabled: true, authMethod: 'session' });
    mockStoreState.messages = [{ id: 'reply', role: 'assistant', content: '独立用户分析' }];
    render(<MemoryRouter><ChatPage /></MemoryRouter>);
    expect(await screen.findByText('独立用户分析')).toBeInTheDocument();
    expect(mockGetSystemConfig).not.toHaveBeenCalled();
    expect(screen.queryByRole('checkbox', { name: /上下文压缩/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '发送到已配置的通知机器人/邮箱' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '导出会话为 Markdown 文件' })).toBeInTheDocument();
  });

  it('discards local draft and stock context when the user identity changes', async () => {
    Object.assign(mockAccessState, { accessEnabled: true, authMethod: 'session' });
    const view = render(<MemoryRouter initialEntries={['/chat?stock=600519']}><ChatPage /></MemoryRouter>);
    expect(await screen.findByRole('button', { name: '加入自选' })).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '用户一的草稿' } });
    mockAccessState.user = { id: 2, name: '用户二', status: 'approved' };
    view.rerender(<MemoryRouter><ChatPage /></MemoryRouter>);
    expect(screen.getByRole('textbox')).toHaveValue('');
    expect(screen.queryByRole('button', { name: '加入自选' })).not.toBeInTheDocument();
    expect(mockStartStream).not.toHaveBeenCalled();
  });

  it('restores a URL session without sending and preserves it when consuming a follow-up prompt', async () => {
    const router = createMemoryRouter([{ path: '/chat', element: <ChatPage /> }], { initialEntries: ['/chat?session=saved-a&prompt=%E8%BF%BD%E9%97%AE&stock=600519&name=茅台'] });
    render(<RouterProvider router={router} />);
    expect(await screen.findByDisplayValue('追问')).toBeInTheDocument();
    expect(mockLoadInitialSession).toHaveBeenCalledWith('saved-a');
    await waitFor(() => { expect(router.state.location.search).toBe('?session=saved-a'); });
    await act(async () => { await router.navigate('/chat?session=saved-b'); });
    expect(mockSwitchSession).toHaveBeenCalledWith('saved-b');
    expect(screen.getByRole('textbox')).toHaveValue('');
    expect(mockStartStream).not.toHaveBeenCalled();
  });

  describe('watchlist research prompt identity', () => {
    const prompt = '请研究华懋科技（603306.SH）。比较已披露事实和市场预期，列出需要继续验证的问题。';
    const route = `/chat?stock=603306.SH&name=${encodeURIComponent('华懋科技')}&prompt=${encodeURIComponent(prompt)}`;
    const expectedContext = { stock_code: '603306.SH', stock_name: '华懋科技' };

    it('uses the explicit stock of a prepared research prompt instead of an existing conversation stock without auto-sending', async () => {
      mockStoreState.messages = [{ id: 'old-a', role: 'user', content: '请分析 600519' }];
      const router = createMemoryRouter([{ path: '/chat', element: <ChatPage /> }], {
        initialEntries: [`${route}&recordId=123`],
      });
      render(<RouterProvider router={router} />);

      expect(await screen.findByDisplayValue(prompt)).toBeInTheDocument();
      expect(router.state.location.search).toBe('');
      expect(historyApi.getDetail).not.toHaveBeenCalled();
      expect(mockStartStream).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole('button', { name: '发送' }));
      await waitFor(() => expect(mockStartStream).toHaveBeenCalledExactlyOnceWith(
        expect.objectContaining({ message: prompt, session_id: 'session-1', context: expectedContext }),
        expect.any(Object),
      ));

      fireEvent.change(screen.getByRole('textbox'), { target: { value: '继续核对经营现金流' } });
      fireEvent.click(screen.getByRole('button', { name: '发送' }));
      await waitFor(() => expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({ message: '继续核对经营现金流', context: expectedContext }),
        expect.any(Object),
      ));
    });

    it('does not let later restored history replace the explicit prepared research stock', async () => {
      mockStoreState.messagesLoading = true;
      const view = render(<MemoryRouter initialEntries={[route]}><ChatPage /></MemoryRouter>);
      expect(await screen.findByDisplayValue(prompt)).toBeInTheDocument();
      expect(screen.getByRole('textbox')).toBeDisabled();

      mockStoreState.messages = [{ id: 'late-a', role: 'user', content: '请分析 600519' }];
      mockStoreState.messagesLoading = false;
      view.rerender(<MemoryRouter initialEntries={[route]}><ChatPage /></MemoryRouter>);
      fireEvent.click(screen.getByRole('button', { name: '发送' }));
      await waitFor(() => expect(mockStartStream).toHaveBeenCalledExactlyOnceWith(
        expect.objectContaining({ message: prompt, context: expectedContext }),
        expect.any(Object),
      ));
    });

    it('discards a pending old report hydration when a new prepared research stock arrives on the same route', async () => {
      const oldReport = createDeferred<Awaited<ReturnType<typeof historyApi.getDetail>>>();
      vi.mocked(historyApi.getDetail).mockImplementationOnce(() => oldReport.promise);
      const router = createMemoryRouter([{ path: '/chat', element: <ChatPage /> }], {
        initialEntries: ['/chat?stock=600519&name=茅台&recordId=123'],
      });
      render(<RouterProvider router={router} />);
      expect(await screen.findByDisplayValue('请深入分析 茅台(600519)')).toBeInTheDocument();
      expect(screen.getByText('正在加载历史分析上下文；现在可直接发送追问。')).toBeInTheDocument();

      await act(async () => { await router.navigate(route); });
      expect(await screen.findByDisplayValue(prompt)).toBeInTheDocument();
      expect(screen.queryByText('正在加载历史分析上下文；现在可直接发送追问。')).not.toBeInTheDocument();
      await act(async () => {
        oldReport.resolve({
          meta: { id: 123, queryId: 'old-a', stockCode: '600519', stockName: '茅台', reportType: 'detailed', createdAt: '2026-09-14T00:00:00Z' },
          summary: { analysisSummary: '旧公司报告', operationAdvice: '', trendPrediction: '', sentimentScore: 0 },
        });
      });
      expect(historyApi.getDetail).toHaveBeenCalledExactlyOnceWith(123);
      expect(mockStartStream).not.toHaveBeenCalled();

      fireEvent.click(screen.getByRole('button', { name: '发送' }));
      await waitFor(() => expect(mockStartStream).toHaveBeenCalledExactlyOnceWith(
        expect.objectContaining({ message: prompt, context: expectedContext }),
        expect.any(Object),
      ));
    });

    it('keeps genuine multi-stock comparison anchored to the prepared research stock', async () => {
      mockStoreState.messages = [{ id: 'old-a', role: 'user', content: '请分析 600519' }];
      render(<MemoryRouter initialEntries={[route]}><ChatPage /></MemoryRouter>);
      await screen.findByDisplayValue(prompt);
      const comparison = '比较 300308.SZ 和 603306.SH 的现金流';
      fireEvent.change(screen.getByRole('textbox'), { target: { value: comparison } });
      fireEvent.click(screen.getByRole('button', { name: '发送' }));
      await waitFor(() => expect(mockStartStream).toHaveBeenCalledExactlyOnceWith(
        expect.objectContaining({ message: comparison, context: expectedContext }),
        expect.any(Object),
      ));
    });

    it('lets an explicit edited stock switch override the prepared research identity', async () => {
      render(<MemoryRouter initialEntries={[route]}><ChatPage /></MemoryRouter>);
      await screen.findByDisplayValue(prompt);
      fireEvent.change(screen.getByRole('textbox'), { target: { value: '改看 AAPL 的现金流' } });
      fireEvent.click(screen.getByRole('button', { name: '发送' }));
      await waitFor(() => expect(mockStartStream).toHaveBeenCalledExactlyOnceWith(
        expect.objectContaining({
          message: '改看 AAPL 的现金流', context: { stock_code: 'AAPL', stock_name: null },
        }),
        expect.any(Object),
      ));
    });
  });
});

describe('ChatPage', () => {
  it('renders a fixed workspace shell with independent session and message viewports', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByTestId('chat-workspace')).toBeInTheDocument();
    expect(screen.getByTestId('chat-session-list-scroll')).toBeInTheDocument();
    expect(screen.getByTestId('chat-message-scroll')).toBeInTheDocument();
    expect(mockLoadInitialSession).toHaveBeenCalled();
    expect(mockClearCompletionBadge).toHaveBeenCalled();
  });

  it('loads and saves the global context compression setting from the chat input area', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const compressionToggle = await screen.findByRole('checkbox', { name: /上下文压缩/ });

    await waitFor(() => {
      expect(compressionToggle).not.toBeDisabled();
    });

    expect(compressionToggle).not.toBeChecked();

    fireEvent.click(compressionToggle);

    await waitFor(() => {
      expect(mockUpdateSystemConfig).toHaveBeenCalledWith({
        configVersion: 'cfg-v1',
        maskToken: 'mask-token',
        reloadNow: true,
        items: [
          {
            key: 'AGENT_CONTEXT_COMPRESSION_ENABLED',
            value: 'true',
          },
        ],
      });
    });

    expect(compressionToggle).toBeChecked();
    expect(screen.getByText('已启用')).toBeInTheDocument();
  });

  it('rolls back the context compression switch when saving fails', async () => {
    mockGetSystemConfig.mockResolvedValue({
      configVersion: 'cfg-v1',
      maskToken: 'mask-token',
      items: [
        {
          key: 'AGENT_CONTEXT_COMPRESSION_ENABLED',
          value: 'true',
          rawValueExists: true,
          isMasked: false,
        },
      ],
    });
    mockUpdateSystemConfig.mockRejectedValue(
      createParsedApiError({
        title: '保存失败',
        message: '配置服务不可用',
        category: 'unknown',
      }),
    );

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const compressionToggle = await screen.findByRole('checkbox', { name: /上下文压缩/ });

    await waitFor(() => {
      expect(compressionToggle).toBeChecked();
      expect(compressionToggle).not.toBeDisabled();
    });

    fireEvent.click(compressionToggle);

    await waitFor(() => {
      expect(mockUpdateSystemConfig).toHaveBeenCalledWith(expect.objectContaining({
        items: [
          {
            key: 'AGENT_CONTEXT_COMPRESSION_ENABLED',
            value: 'false',
          },
        ],
      }));
      expect(compressionToggle).toBeChecked();
    });
    expect(screen.getByText('配置服务不可用')).toBeInTheDocument();
  });

  it('does not switch when clicking the current session card', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const sessionCard = await screen.findByRole('button', {
      name: /切换到对话 请简要分析 600519/,
    });

    fireEvent.click(sessionCard);
    expect(mockSwitchSession).not.toHaveBeenCalled();
    expect(sessionCard).toHaveAttribute('aria-current', 'page');
  });

  it('renders a separate delete button for each session and opens confirmation without switching', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const deleteButton = await screen.findByRole('button', {
      name: /删除对话 请简要分析 600519/,
    });

    fireEvent.click(deleteButton);

    expect(mockSwitchSession).not.toHaveBeenCalled();
    expect(await screen.findByText('删除后，该对话将不可恢复，确认删除吗？')).toBeInTheDocument();
  });

  it('hides header actions when there are no messages', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByRole('heading', { name: '问股' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '导出会话' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '发送到已配置的通知机器人/邮箱' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '历史对话' })).toBeInTheDocument();
  });

  it('exports the current session from the header action', async () => {
    mockStoreState.messages = [
      { id: 'user-1', role: 'user', content: '请分析 600519' },
      { id: 'assistant-1', role: 'assistant', content: '趋势偏强', skillName: '趋势分析' },
    ];

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    fireEvent.click(await screen.findByRole('button', { name: '导出会话为 Markdown 文件' }));

    expect(mockDownloadSession).toHaveBeenCalledWith(mockStoreState.messages);
    expect(mockFormatSessionAsMarkdown).not.toHaveBeenCalled();
  });

  it('renders assistant skill labels with shared badge semantics', async () => {
    mockStoreState.messages = [
      { id: 'assistant-1', role: 'assistant', content: '趋势偏强', skillName: '趋势分析' },
    ];

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const skillBadge = await screen.findByLabelText('技能 趋势分析');
    expect(skillBadge).toBeInTheDocument();
    expect(skillBadge).toHaveTextContent('趋势分析');
  });

  it('renders assistant multi-skill labels with shared badge semantics', async () => {
    mockStoreState.messages = [
      {
        id: 'assistant-1',
        role: 'assistant',
        content: '趋势偏强',
        skills: ['bull_trend', 'ma_golden_cross'],
        skillNames: ['趋势分析', '均线金叉'],
      },
    ];

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const skillBadge = await screen.findByLabelText('技能 趋势分析、均线金叉');
    expect(skillBadge).toBeInTheDocument();
    expect(skillBadge).toHaveTextContent('趋势分析、均线金叉');
  });

  it('selects the default skill after loading skills', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByRole('checkbox', { name: '趋势分析' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: '通用分析' })).not.toBeChecked();
  });

  it('sends multiple selected skills in order', async () => {
    mockGetSkills.mockResolvedValue({
      skills: [
        { id: 'bull_trend', name: '趋势分析', description: '默认趋势' },
        { id: 'ma_golden_cross', name: '均线金叉', description: '均线交叉' },
      ],
      default_skill_id: 'bull_trend',
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    fireEvent.click(await screen.findByRole('checkbox', { name: '均线金叉' }));
    fireEvent.change(screen.getByPlaceholderText(/分析 600519/), {
      target: { value: '分析 600519' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalledWith(
        expect.objectContaining({
          message: '分析 600519',
          skills: ['bull_trend', 'ma_golden_cross'],
        }),
        expect.objectContaining({
          skillNames: ['趋势分析', '均线金叉'],
          skillName: '趋势分析、均线金叉',
        }),
      );
    });
  });

  it('collapses the mobile skill picker by default and keeps selected skills when sending', async () => {
    mockGetSkills.mockResolvedValue({
      skills: [
        { id: 'bull_trend', name: '趋势分析', description: '默认趋势' },
        { id: 'ma_golden_cross', name: '均线金叉', description: '均线交叉' },
      ],
      default_skill_id: 'bull_trend',
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const mobileToggle = await screen.findByRole('button', { name: '展开策略选择' });
    const skillPanel = screen.getByTestId('chat-skill-picker-panel');
    expect(mobileToggle).toHaveAttribute('aria-expanded', 'false');
    expect(skillPanel).toHaveClass('hidden');

    fireEvent.click(mobileToggle);

    expect(screen.getByRole('button', { name: '收起策略选择' })).toHaveAttribute('aria-expanded', 'true');
    expect(skillPanel).not.toHaveClass('hidden');
    expect(skillPanel).toHaveClass('flex');

    fireEvent.click(screen.getByRole('checkbox', { name: '均线金叉' }));
    fireEvent.change(screen.getByPlaceholderText(/分析 600519/), {
      target: { value: '分析 600519' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalledWith(
        expect.objectContaining({
          message: '分析 600519',
          skills: ['bull_trend', 'ma_golden_cross'],
        }),
        expect.objectContaining({
          skillName: '趋势分析、均线金叉',
        }),
      );
    });

    expect(screen.getByRole('button', { name: '展开策略选择' })).toHaveAttribute('aria-expanded', 'false');
    expect(skillPanel).toHaveClass('hidden');
  });

  it('omits skills when all concrete skills are cleared', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    fireEvent.click(await screen.findByRole('checkbox', { name: '趋势分析' }));
    expect(screen.getByRole('checkbox', { name: '通用分析' })).toBeChecked();

    fireEvent.change(screen.getByPlaceholderText(/分析 600519/), {
      target: { value: '分析 AAPL' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalled();
    });
    const lastCall = mockStartStream.mock.calls[mockStartStream.mock.calls.length - 1];
    expect(lastCall[0]).toEqual(expect.objectContaining({ message: '分析 AAPL' }));
    expect(lastCall[0]).not.toHaveProperty('skills');
    expect(lastCall[1]).toEqual(expect.objectContaining({
      skillNames: ['通用'],
      skillName: '通用',
    }));
  });

  it('caps concrete skill selection at three and re-enables choices after unselecting', async () => {
    mockGetSkills.mockResolvedValue({
      skills: [
        { id: 'bull_trend', name: '趋势分析', description: '默认趋势' },
        { id: 'ma_golden_cross', name: '均线金叉', description: '均线交叉' },
        { id: 'chan_theory', name: '缠论', description: '结构分析' },
        { id: 'wave_theory', name: '波浪理论', description: '波浪分析' },
      ],
      default_skill_id: 'bull_trend',
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    fireEvent.click(await screen.findByRole('checkbox', { name: '均线金叉' }));
    fireEvent.click(screen.getByRole('checkbox', { name: '缠论' }));

    const wave = screen.getByRole('checkbox', { name: '波浪理论' });
    expect(wave).toBeDisabled();

    fireEvent.click(screen.getByRole('checkbox', { name: '均线金叉' }));
    expect(wave).not.toBeDisabled();
  });

  it('quick questions override the current multi-skill selection', async () => {
    mockGetSkills.mockResolvedValue({
      skills: [
        { id: 'bull_trend', name: '趋势分析', description: '默认趋势' },
        { id: 'ma_golden_cross', name: '均线金叉', description: '均线交叉' },
        { id: 'chan_theory', name: '缠论', description: '结构分析' },
      ],
      default_skill_id: 'bull_trend',
    });

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    fireEvent.click(await screen.findByRole('checkbox', { name: '均线金叉' }));
    fireEvent.click(screen.getByRole('button', { name: '用缠论分析茅台' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalledWith(
        expect.objectContaining({
          message: '用缠论分析茅台',
          skills: ['chan_theory'],
        }),
        expect.objectContaining({
          skillNames: ['缠论'],
          skillName: '缠论',
        }),
      );
    });
  });

  it('keeps assistant message actions directly activatable in the DOM', async () => {
    mockStoreState.messages = [
      { id: 'assistant-1', role: 'assistant', content: '趋势偏强', skillName: '趋势分析' },
    ];

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const exportButton = await screen.findByRole('button', { name: '导出此条消息为 Markdown' });
    const actionGroup = exportButton.parentElement;

    expect(actionGroup).toHaveClass('chat-message-actions');
    expect(actionGroup?.className).not.toMatch(/pointer-events-none|opacity-0/);
  });

  it('sends exported markdown to notification channel and shows success feedback', async () => {
    mockStoreState.messages = [
      { id: 'user-1', role: 'user', content: '请分析 600519' },
      { id: 'assistant-1', role: 'assistant', content: '趋势偏强', skillName: '趋势分析' },
    ];
    mockFormatSessionAsMarkdown.mockReturnValue('# exported markdown');

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    fireEvent.click(await screen.findByRole('button', { name: '发送到已配置的通知机器人/邮箱' }));

    await waitFor(() => {
      expect(mockFormatSessionAsMarkdown).toHaveBeenCalledWith(mockStoreState.messages);
      expect(mockSendChat).toHaveBeenCalledWith('# exported markdown');
    });

    expect(await screen.findByText('已发送到通知渠道')).toBeInTheDocument();
  });

  it('shows parsed error feedback when notification delivery fails', async () => {
    mockStoreState.messages = [
      { id: 'user-1', role: 'user', content: '请分析 AAPL' },
      { id: 'assistant-1', role: 'assistant', content: '短线震荡', skillName: '趋势分析' },
    ];
    mockSendChat.mockRejectedValue(
      createParsedApiError({
        title: '发送失败',
        message: '通知渠道不可用',
        category: 'unknown',
      }),
    );

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    fireEvent.click(await screen.findByRole('button', { name: '发送到已配置的通知机器人/邮箱' }));

    expect(await screen.findByText('通知渠道不可用')).toBeInTheDocument();
  });

  it('prevents duplicate notification sends while the request is in flight', async () => {
    mockStoreState.messages = [
      { id: 'user-1', role: 'user', content: '请分析 TSLA' },
      { id: 'assistant-1', role: 'assistant', content: '波动较大', skillName: '趋势分析' },
    ];
    const deferred = createDeferred<{ success: boolean }>();
    mockSendChat.mockImplementation(() => deferred.promise);

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const sendButton = await screen.findByRole('button', { name: '发送到已配置的通知机器人/邮箱' });
    fireEvent.click(sendButton);

    await waitFor(() => {
      expect(mockSendChat).toHaveBeenCalledTimes(1);
      expect(sendButton).toBeDisabled();
    });

    fireEvent.click(sendButton);
    expect(mockSendChat).toHaveBeenCalledTimes(1);

    deferred.resolve({ success: true });

    await waitFor(() => {
      expect(sendButton).not.toBeDisabled();
    });
  });

  it('allows sending with base follow-up context before report hydration completes', async () => {
    const deferred = createDeferred<Awaited<ReturnType<typeof historyApi.getDetail>>>();

    vi.mocked(historyApi.getDetail).mockImplementation(() => deferred.promise);

    render(
      <MemoryRouter initialEntries={['/chat?stock=600519&name=%E8%B4%B5%E5%B7%9E%E8%8C%85%E5%8F%B0&recordId=1']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByDisplayValue('请深入分析 贵州茅台(600519)')).toBeInTheDocument();

    const sendButton = screen.getByRole('button', { name: /发送|处理中\.\.\./ });
    expect(sendButton).not.toBeDisabled();
    expect(screen.getByText('正在加载历史分析上下文；现在可直接发送追问。')).toBeInTheDocument();

    fireEvent.click(sendButton);

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalledWith(
        expect.objectContaining({
          message: '请深入分析 贵州茅台(600519)',
          context: {
            stock_code: '600519',
            stock_name: '贵州茅台',
          },
        }),
        expect.objectContaining({
          skillName: '趋势分析',
        }),
      );
    });

    deferred.resolve({
      meta: {
        id: 1,
        queryId: 'q-1',
        stockCode: '600519',
        stockName: '贵州茅台',
        reportType: 'detailed',
        createdAt: '2026-03-18T08:00:00Z',
        currentPrice: 1523.6,
        changePct: 1.8,
      },
      summary: {
        analysisSummary: '趋势延续',
        operationAdvice: '继续观察',
        trendPrediction: '高位震荡',
        sentimentScore: 78,
      },
      strategy: {
        stopLoss: '1450',
      },
    });

    await waitFor(() => {
      expect(screen.queryByText('正在加载历史分析上下文；现在可直接发送追问。')).not.toBeInTheDocument();
    });

    fireEvent.change(screen.getByPlaceholderText(/分析 600519/), {
      target: { value: '继续分析成交量' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: '继续分析成交量',
          context: expect.objectContaining({
            stock_code: '600519',
            stock_name: '贵州茅台',
          }),
        }),
        expect.objectContaining({
          skillName: '趋势分析',
        }),
      );
    });

    fireEvent.change(screen.getByPlaceholderText(/分析 600519/), {
      target: { value: '如果不考虑 TTM 呢' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: '如果不考虑 TTM 呢',
          context: expect.objectContaining({
            stock_code: '600519',
            stock_name: '贵州茅台',
          }),
        }),
        expect.objectContaining({
          skillName: '趋势分析',
        }),
      );
    });
  });

  it('uses hydrated report context when it finishes before sending', async () => {
    vi.mocked(historyApi.getDetail).mockResolvedValue({
      meta: {
        id: 1,
        queryId: 'q-1',
        stockCode: '600519',
        stockName: '贵州茅台',
        reportType: 'detailed',
        createdAt: '2026-03-18T08:00:00Z',
        currentPrice: 1523.6,
        changePct: 1.8,
      },
      summary: {
        analysisSummary: '趋势延续',
        operationAdvice: '继续观察',
        trendPrediction: '高位震荡',
        sentimentScore: 78,
      },
      strategy: {
        stopLoss: '1450',
      },
    });

    render(
      <MemoryRouter initialEntries={['/chat?stock=600519&name=%E8%B4%B5%E5%B7%9E%E8%8C%85%E5%8F%B0&recordId=1']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByDisplayValue('请深入分析 贵州茅台(600519)')).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.queryByText('正在加载历史分析上下文；现在可直接发送追问。')).not.toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalledWith(
        expect.objectContaining({
          message: '请深入分析 贵州茅台(600519)',
          context: expect.objectContaining({
            stock_code: '600519',
            stock_name: '贵州茅台',
            previous_price: 1523.6,
            previous_change_pct: 1.8,
            previous_strategy: expect.objectContaining({
              stopLoss: '1450',
            }),
          }),
        }),
        expect.objectContaining({
          skillName: '趋势分析',
        }),
      );
    });
  });

  it('falls back to base stock context when recordId is missing', async () => {
    render(
      <MemoryRouter initialEntries={['/chat?stock=AAPL']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByDisplayValue('请深入分析 AAPL')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalledWith(
        expect.objectContaining({
          message: '请深入分析 AAPL',
          context: {
            stock_code: 'AAPL',
            stock_name: null,
          },
        }),
        expect.objectContaining({
          skillName: '趋势分析',
        }),
      );
    });
    expect(historyApi.getDetail).not.toHaveBeenCalled();

    fireEvent.change(screen.getByPlaceholderText(/分析 600519/), {
      target: { value: '继续看估值' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: '继续看估值',
          context: {
            stock_code: 'AAPL',
            stock_name: null,
          },
        }),
        expect.objectContaining({
          skillName: '趋势分析',
        }),
      );
    });
  });

  it('switches active stock context for explicit switch messages', async () => {
    render(
      <MemoryRouter initialEntries={['/chat?stock=600519&name=%E8%B4%B5%E5%B7%9E%E8%8C%85%E5%8F%B0']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByDisplayValue('请深入分析 贵州茅台(600519)')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/分析 600519/), {
      target: { value: '换成 AAPL 看看' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: '换成 AAPL 看看',
          context: {
            stock_code: 'AAPL',
            stock_name: null,
          },
        }),
        expect.objectContaining({
          skillName: '趋势分析',
        }),
      );
    });
  });

  it('switches to the single new stock when the current stock appears first', async () => {
    render(
      <MemoryRouter initialEntries={['/chat?stock=600519&name=%E8%B4%B5%E5%B7%9E%E8%8C%85%E5%8F%B0']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByDisplayValue('请深入分析 贵州茅台(600519)')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/分析 600519/), {
      target: { value: '先不看 600519，换成 AAPL 看看' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: '先不看 600519，换成 AAPL 看看',
          context: {
            stock_code: 'AAPL',
            stock_name: null,
          },
        }),
        expect.objectContaining({
          skillName: '趋势分析',
        }),
      );
    });

    fireEvent.change(screen.getByPlaceholderText(/分析 600519/), {
      target: { value: '继续看支撑位' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: '继续看支撑位',
          context: {
            stock_code: 'AAPL',
            stock_name: null,
          },
        }),
        expect.objectContaining({
          skillName: '趋势分析',
        }),
      );
    });
  });

  it('keeps active stock context for compare messages', async () => {
    render(
      <MemoryRouter initialEntries={['/chat?stock=600519&name=%E8%B4%B5%E5%B7%9E%E8%8C%85%E5%8F%B0']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByDisplayValue('请深入分析 贵州茅台(600519)')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/分析 600519/), {
      target: { value: '比较 600519 和 AAPL' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: '比较 600519 和 AAPL',
          context: {
            stock_code: '600519',
            stock_name: '贵州茅台',
          },
        }),
        expect.objectContaining({
          skillName: '趋势分析',
        }),
      );
    });
  });

  it('keeps active stock context for difference-style compare messages', async () => {
    render(
      <MemoryRouter initialEntries={['/chat?stock=600519&name=%E8%B4%B5%E5%B7%9E%E8%8C%85%E5%8F%B0']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByDisplayValue('请深入分析 贵州茅台(600519)')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/分析 600519/), {
      target: { value: '分析 600519 和 AAPL 的差异' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: '分析 600519 和 AAPL 的差异',
          context: {
            stock_code: '600519',
            stock_name: '贵州茅台',
          },
        }),
        expect.objectContaining({
          skillName: '趋势分析',
        }),
      );
    });
  });

  it('keeps active stock context when the compared stock appears first', async () => {
    render(
      <MemoryRouter initialEntries={['/chat?stock=600519&name=%E8%B4%B5%E5%B7%9E%E8%8C%85%E5%8F%B0']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByDisplayValue('请深入分析 贵州茅台(600519)')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/分析 600519/), {
      target: { value: '分析 AAPL 和 600519 的差异' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: '分析 AAPL 和 600519 的差异',
          context: {
            stock_code: '600519',
            stock_name: '贵州茅台',
          },
        }),
        expect.objectContaining({
          skillName: '趋势分析',
        }),
      );
    });
  });

  it('keeps active stock context for choice-style multi-stock messages', async () => {
    render(
      <MemoryRouter initialEntries={['/chat?stock=600519&name=%E8%B4%B5%E5%B7%9E%E8%8C%85%E5%8F%B0']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByDisplayValue('请深入分析 贵州茅台(600519)')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/分析 600519/), {
      target: { value: 'AAPL 和 TSLA 哪个更值得买' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: 'AAPL 和 TSLA 哪个更值得买',
          context: {
            stock_code: '600519',
            stock_name: '贵州茅台',
          },
        }),
        expect.objectContaining({
          skillName: '趋势分析',
        }),
      );
    });
  });

  it('switches active stock context for single-stock difference phrasing', async () => {
    render(
      <MemoryRouter initialEntries={['/chat?stock=600519&name=%E8%B4%B5%E5%B7%9E%E8%8C%85%E5%8F%B0']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByDisplayValue('请深入分析 贵州茅台(600519)')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/分析 600519/), {
      target: { value: '分析 AAPL 的差异化优势' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: '分析 AAPL 的差异化优势',
          context: {
            stock_code: 'AAPL',
            stock_name: null,
          },
        }),
        expect.objectContaining({
          skillName: '趋势分析',
        }),
      );
    });
  });

  it('switches active stock context for lowercase US ticker switch messages', async () => {
    render(
      <MemoryRouter initialEntries={['/chat?stock=600519&name=%E8%B4%B5%E5%B7%9E%E8%8C%85%E5%8F%B0']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByDisplayValue('请深入分析 贵州茅台(600519)')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/分析 600519/), {
      target: { value: '分析tsla' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: '分析tsla',
          context: {
            stock_code: 'TSLA',
            stock_name: null,
          },
        }),
        expect.objectContaining({
          skillName: '趋势分析',
        }),
      );
    });
  });

  it('keeps active stock context when clicking the current session', async () => {
    render(
      <MemoryRouter initialEntries={['/chat?stock=600519&name=%E8%B4%B5%E5%B7%9E%E8%8C%85%E5%8F%B0']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByDisplayValue('请深入分析 贵州茅台(600519)')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '切换到对话 请简要分析 600519' }));
    expect(mockSwitchSession).not.toHaveBeenCalled();

    fireEvent.change(screen.getByPlaceholderText(/分析 600519/), {
      target: { value: '继续看成交量' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: '继续看成交量',
          context: {
            stock_code: '600519',
            stock_name: '贵州茅台',
          },
        }),
        expect.objectContaining({
          skillName: '趋势分析',
        }),
      );
    });
  });

  it('restores active stock context from loaded session messages', async () => {
    mockStoreState.messages = [
      { id: 'm-1', role: 'user', content: '请分析 600519' },
      { id: 'm-2', role: 'assistant', content: '600519 分析结果' },
      { id: 'm-3', role: 'user', content: '先不看 600519，换成 AAPL 看看' },
      { id: 'm-4', role: 'assistant', content: 'AAPL 分析结果' },
    ];

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByTestId('chat-workspace')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/分析 600519/), {
      target: { value: '继续看支撑位' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: '继续看支撑位',
          context: {
            stock_code: 'AAPL',
            stock_name: null,
          },
        }),
        expect.objectContaining({
          skillName: '趋势分析',
        }),
      );
    });
  });

  it('clears active stock context when starting a new chat or switching sessions', async () => {
    mockStoreState.sessions = [
      ...mockStoreState.sessions,
      {
        session_id: 'session-2',
        title: '旧会话',
        message_count: 1,
        created_at: '2026-03-16T09:00:00Z',
        last_active: '2026-03-16T09:05:00Z',
      },
    ];

    const { unmount } = render(
      <MemoryRouter initialEntries={['/chat?stock=600519&name=%E8%B4%B5%E5%B7%9E%E8%8C%85%E5%8F%B0']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByDisplayValue('请深入分析 贵州茅台(600519)')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '开启新对话' }));
    expect(mockStartNewChat).toHaveBeenCalled();

    fireEvent.change(screen.getByPlaceholderText(/分析 600519/), {
      target: { value: '继续看成交量' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: '继续看成交量',
          context: undefined,
        }),
        expect.objectContaining({
          skillName: '趋势分析',
        }),
      );
    });

    unmount();
    mockStartStream.mockClear();

    render(
      <MemoryRouter initialEntries={['/chat?stock=600519&name=%E8%B4%B5%E5%B7%9E%E8%8C%85%E5%8F%B0']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByDisplayValue('请深入分析 贵州茅台(600519)')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '切换到对话 旧会话' }));
    expect(mockSwitchSession).toHaveBeenCalledWith('session-2');

    fireEvent.change(screen.getByPlaceholderText(/分析 600519/), {
      target: { value: '继续看成交量' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: '继续看成交量',
          context: undefined,
        }),
        expect.objectContaining({
          skillName: '趋势分析',
        }),
      );
    });
  });

  it('clears active stock context when deleting the current session', async () => {
    render(
      <MemoryRouter initialEntries={['/chat?stock=600519&name=%E8%B4%B5%E5%B7%9E%E8%8C%85%E5%8F%B0']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByDisplayValue('请深入分析 贵州茅台(600519)')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '删除对话 请简要分析 600519' }));
    fireEvent.click(screen.getByRole('button', { name: '删除' }));

    await waitFor(() => {
      expect(mockDeleteChatSession).toHaveBeenCalledWith('session-1');
    });
    expect(mockStartNewChat).toHaveBeenCalled();

    fireEvent.change(screen.getByPlaceholderText(/分析 600519/), {
      target: { value: '继续看成交量' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenLastCalledWith(
        expect.objectContaining({
          message: '继续看成交量',
          context: undefined,
        }),
        expect.objectContaining({
          skillName: '趋势分析',
        }),
      );
    });
  });

  it('ignores malformed follow-up query params', async () => {
    render(
      <MemoryRouter initialEntries={['/chat?stock=%3Cscript%3E&name=Bad%0AName&recordId=abc']}>
        <ChatPage />
      </MemoryRouter>
    );

    expect(await screen.findByRole('heading', { name: '问股' })).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/分析 600519/)).toHaveValue('');
    expect(historyApi.getDetail).not.toHaveBeenCalled();
  });

  it('reprocesses follow-up query params when navigating to the same chat route again', async () => {
    const firstDeferred = createDeferred<Awaited<ReturnType<typeof historyApi.getDetail>>>();
    const secondDeferred = createDeferred<Awaited<ReturnType<typeof historyApi.getDetail>>>();

    vi.mocked(historyApi.getDetail)
      .mockImplementationOnce(() => firstDeferred.promise)
      .mockImplementationOnce(() => secondDeferred.promise);

    const router = createMemoryRouter(
      [{ path: '/chat', element: <ChatPage /> }],
      {
        initialEntries: ['/chat?stock=600519&name=%E8%B4%B5%E5%B7%9E%E8%8C%85%E5%8F%B0&recordId=1'],
      },
    );

    render(<RouterProvider router={router} />);

    expect(await screen.findByDisplayValue('请深入分析 贵州茅台(600519)')).toBeInTheDocument();
    expect(screen.getByText('正在加载历史分析上下文；现在可直接发送追问。')).toBeInTheDocument();

    await router.navigate('/chat?stock=AAPL&name=Apple&recordId=2');

    expect(await screen.findByDisplayValue('请深入分析 Apple(AAPL)')).toBeInTheDocument();

    firstDeferred.resolve({
      meta: {
        id: 1,
        queryId: 'q-1',
        stockCode: '600519',
        stockName: '贵州茅台',
        reportType: 'detailed',
        createdAt: '2026-03-18T08:00:00Z',
        currentPrice: 1523.6,
        changePct: 1.8,
      },
      summary: {
        analysisSummary: '趋势延续',
        operationAdvice: '继续观察',
        trendPrediction: '高位震荡',
        sentimentScore: 78,
      },
      strategy: {
        stopLoss: '1450',
      },
    });

    secondDeferred.resolve({
      meta: {
        id: 2,
        queryId: 'q-2',
        stockCode: 'AAPL',
        stockName: 'Apple',
        reportType: 'detailed',
        createdAt: '2026-03-18T09:00:00Z',
        currentPrice: 211.5,
        changePct: 2.4,
      },
      summary: {
        analysisSummary: '趋势走强',
        operationAdvice: '继续持有',
        trendPrediction: '短线偏强',
        sentimentScore: 81,
      },
      strategy: {
        stopLoss: '205',
      },
    });

    await waitFor(() => {
      expect(screen.queryByText('正在加载历史分析上下文；现在可直接发送追问。')).not.toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(mockStartStream).toHaveBeenCalledWith(
        expect.objectContaining({
          message: '请深入分析 Apple(AAPL)',
          context: expect.objectContaining({
            stock_code: 'AAPL',
            stock_name: 'Apple',
            previous_price: 211.5,
            previous_change_pct: 2.4,
            previous_strategy: expect.objectContaining({
              stopLoss: '205',
            }),
          }),
        }),
        expect.objectContaining({
          skillName: '趋势分析',
        }),
      );
    });
  });

  it('shows a jump-to-latest action when new content arrives while the user is away from bottom', async () => {
    mockStoreState.messages = [
      { id: 'user-1', role: 'user', content: '请分析 600519' },
      { id: 'assistant-1', role: 'assistant', content: '趋势偏强', skillName: '趋势分析' },
    ];

    const { rerender } = render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const viewport = await screen.findByTestId('chat-message-scroll');
    Object.defineProperty(viewport, 'scrollTop', { configurable: true, value: 0 });
    Object.defineProperty(viewport, 'clientHeight', { configurable: true, value: 400 });
    Object.defineProperty(viewport, 'scrollHeight', { configurable: true, value: 1200 });

    fireEvent.scroll(viewport);

    mockStoreState.messages = [
      ...mockStoreState.messages,
      { id: 'assistant-2', role: 'assistant', content: '新的补充分析', skillName: '趋势分析' },
    ];

    rerender(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>
    );

    const jumpButton = await screen.findByRole('button', { name: '查看最新消息' });
    expect(jumpButton).toBeInTheDocument();

    fireEvent.click(jumpButton);

    expect(HTMLElement.prototype.scrollIntoView).toHaveBeenCalled();
  });
});

describe('extractStockCodeFromMessage', () => {
  it('returns 6-digit A-share code', () => {
    expect(extractStockCodeFromMessage('分析 600519 趋势')).toBe('600519');
    expect(extractStockCodeFromMessage('002460')).toBe('002460');
  });

  it('returns HK prefixed code (normalized)', () => {
    expect(extractStockCodeFromMessage('分析 hk00700')).toBe('HK00700');
  });

  it('returns .HK suffix code (normalized to canonical)', () => {
    expect(extractStockCodeFromMessage('00700.HK')).toBe('HK00700');
    expect(extractStockCodeFromMessage('1810.HK')).toBe('HK01810');
  });

  it('returns code with .SH/.SZ suffix (normalized)', () => {
    expect(extractStockCodeFromMessage('看 600519.SH')).toBe('600519');
    expect(extractStockCodeFromMessage('000001.SZ')).toBe('000001');
  });

  it('returns US ticker like AAPL', () => {
    expect(extractStockCodeFromMessage('分析 AAPL 走势')).toBe('AAPL');
    expect(extractStockCodeFromMessage('TSLA')).toBe('TSLA');
    expect(extractStockCodeFromMessage('分析 BRK.B')).toBe('BRK.B');
  });

  it('does NOT return finance abbreviations as tickers', () => {
    expect(extractStockCodeFromMessage('如果不考虑 TTM 呢')).toBeNull();
    expect(extractStockCodeFromMessage('市盈率 TTM 怎么看')).toBeNull();
    expect(extractStockCodeFromMessage('PE 怎么看')).toBeNull();
    expect(extractStockCodeFromMessage('MACD 还没金叉吗')).toBeNull();
    expect(extractStockCodeFromMessage('RSI 怎么看')).toBeNull();
    expect(extractStockCodeFromMessage('WHAT IS PE')).toBeNull();
    expect(extractStockCodeFromMessage('PE IS HIGH')).toBeNull();
    expect(extractStockCodeFromMessage('WHAT IS TTM')).toBeNull();
  });

  it('does NOT return contextual moving-average MA as a ticker', () => {
    expect(extractStockCodeFromMessage('分析 MA 均线')).toBeNull();
    expect(extractStockCodeFromMessage('看看 MA 怎么排列')).toBeNull();
    expect(extractStockCodesFromMessage('MA 和 RSI 的指标怎么看')).toEqual([]);
    expect(extractStockCodeFromMessage('分析 KDJ 指标')).toBeNull();
    expect(extractStockCodeFromMessage('KDJ 怎么看')).toBeNull();
  });

  it('skips finance abbreviations before a real ticker', () => {
    expect(extractStockCodeFromMessage('PE AAPL 怎么看')).toBe('AAPL');
    expect(extractStockCodeFromMessage('TTM AAPL 怎么看')).toBe('AAPL');
    expect(extractStockCodeFromMessage('MACD AAPL 怎么看')).toBe('AAPL');
    expect(extractStockCodeFromMessage('WHAT IS PE AAPL')).toBe('AAPL');
  });

  it('does NOT return exchange prefixes as tickers', () => {
    expect(extractStockCodeFromMessage('分析 SH 走势')).toBeNull();
    expect(extractStockCodeFromMessage('看看 BJ')).toBeNull();
    expect(extractStockCodeFromMessage('HK')).toBeNull();
    expect(extractStockCodeFromMessage('买入 SZ')).toBeNull();
    expect(extractStockCodeFromMessage('US 市场')).toBeNull();
    expect(extractStockCodeFromMessage('SS')).toBeNull();
  });

  it('returns null for messages without stock codes', () => {
    expect(extractStockCodeFromMessage('茅台现在适合买入吗')).toBeNull();
    expect(extractStockCodeFromMessage('大盘走势如何')).toBeNull();
  });

  it('matches prefixed code like SH600519 (normalized)', () => {
    expect(extractStockCodeFromMessage('分析 SH600519')).toBe('600519');
  });

  it('returns SZ-prefixed code when standalone (normalized)', () => {
    expect(extractStockCodeFromMessage('SZ000001')).toBe('000001');
  });

  it('returns all stock codes in message order', () => {
    expect(extractStockCodesFromMessage('分析 600519 和 AAPL 的差异')).toEqual(['600519', 'AAPL']);
    expect(extractStockCodesFromMessage('分析 AAPL 和 600519 的差异')).toEqual(['AAPL', '600519']);
    expect(extractStockCodesFromMessage('AAPL 和 TSLA 哪个更值得买')).toEqual(['AAPL', 'TSLA']);
    expect(extractStockCodesFromMessage('比较 BRK.B 和 AAPL')).toEqual(['BRK.B', 'AAPL']);
  });

  it('extracts lowercase tickers only with explicit stock intent hints', () => {
    expect(extractStockCodesFromMessage('分析tsla')).toEqual(['TSLA']);
    expect(extractStockCodesFromMessage('看看 tsla')).toEqual(['TSLA']);
    expect(extractStockCodesFromMessage('aapl 和 tsla 哪个更值得买')).toEqual(['AAPL', 'TSLA']);
    expect(extractStockCodesFromMessage('hello tsla')).toEqual([]);
  });

  it('returns all HK and A-share variants without exchange affix tokens', () => {
    expect(extractStockCodesFromMessage('比较 01810 和 AAPL')).toEqual(['HK01810', 'AAPL']);
    expect(extractStockCodesFromMessage('比较 1810.HK 和 AAPL')).toEqual(['HK01810', 'AAPL']);
    expect(extractStockCodesFromMessage('比较 600519.SH 和 AAPL')).toEqual(['600519', 'AAPL']);
    expect(extractStockCodesFromMessage('比较 000001.SZ 和 SS')).toEqual(['000001']);
    expect(extractStockCodesFromMessage('比较 SH600519 和 AAPL')).toEqual(['600519', 'AAPL']);
    expect(extractStockCodesFromMessage('比较 SZ000001 和 AAPL')).toEqual(['000001', 'AAPL']);
    expect(extractStockCodesFromMessage('比较 BJ920748 和 AAPL')).toEqual(['920748', 'AAPL']);
    expect(extractStockCodesFromMessage('比较 HK01810 和 AAPL')).toEqual(['HK01810', 'AAPL']);
  });

  it('does not return denied abbreviations in multi-code extraction', () => {
    expect(extractStockCodesFromMessage('如果不考虑 TTM 和 PE')).toEqual([]);
    expect(extractStockCodesFromMessage('MACD AAPL 和 RSI')).toEqual(['AAPL']);
    expect(extractStockCodesFromMessage('KDJ AAPL 怎么看')).toEqual(['AAPL']);
  });
});

describe('watchlist button with code variants', () => {
  it('shows "从自选删除" when canonical code is in watchlist and user inputs variant', async () => {
    mockGetWatchlist.mockResolvedValue(['600519', 'HK01810']);

    render(
      <MemoryRouter>
        <ChatPage />
      </MemoryRouter>,
    );

    const textarea = await screen.findByPlaceholderText(/例如/);
    fireEvent.change(textarea, { target: { value: '分析 600519.SH' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });

    expect(await screen.findByText('从自选删除')).toBeInTheDocument();
  });

  it('shows "从自选删除" for HK variant codes', async () => {
    mockGetWatchlist.mockResolvedValue(['HK01810']);

    render(
      <MemoryRouter>
        <ChatPage />
      </MemoryRouter>,
    );

    const textarea = await screen.findByPlaceholderText(/例如/);
    fireEvent.change(textarea, { target: { value: '分析 1810.HK' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });

    expect(await screen.findByText('从自选删除')).toBeInTheDocument();
  });

  it('matches raw HK watchlist entries before rendering the watchlist action', async () => {
    mockGetWatchlist.mockResolvedValue(['01810']);

    render(
      <MemoryRouter>
        <ChatPage />
      </MemoryRouter>,
    );

    const textarea = await screen.findByPlaceholderText(/例如/);
    fireEvent.change(textarea, { target: { value: '分析 1810.HK' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });

    expect(await screen.findByText('从自选删除')).toBeInTheDocument();
  });

  it('removes the matched raw HK watchlist entry instead of adding a duplicate variant', async () => {
    mockGetWatchlist.mockResolvedValue(['00700']);
    mockRemoveFromWatchlist.mockResolvedValue([]);

    render(
      <MemoryRouter>
        <ChatPage />
      </MemoryRouter>,
    );

    const textarea = await screen.findByPlaceholderText(/例如/);
    fireEvent.change(textarea, { target: { value: '分析 00700.HK' } });
    fireEvent.keyDown(textarea, { key: 'Enter' });
    fireEvent.click(await screen.findByText('从自选删除'));

    await waitFor(() => {
      expect(mockRemoveFromWatchlist).toHaveBeenCalledWith('00700');
    });
    expect(mockAddToWatchlist).not.toHaveBeenCalled();
  });
});
