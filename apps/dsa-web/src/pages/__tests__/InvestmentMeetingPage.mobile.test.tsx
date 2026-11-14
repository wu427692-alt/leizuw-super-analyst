import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import InvestmentMeetingPage from '../InvestmentMeetingPage';

const {
  mockChatState,
  mockStartNewChat,
  mockStartStream,
  mockGetAgentRoles,
} = vi.hoisted(() => {
  const state = {
    loading: false,
    progressSteps: [] as unknown[],
    messages: [] as unknown[],
    messagesLoading: false,
    historyError: null as null,
    chatError: null as { message: string } | null,
    ownerScope: 'user:7' as string | null,
    sessionId: 'store-session-1',
  };
  const startNewChat = vi.fn(() => { state.sessionId = 'research-new-1'; });
  const startStream = vi.fn().mockResolvedValue(undefined);
  const getAgentRoles = vi.fn();
  return {
    mockChatState: state,
    mockStartNewChat: startNewChat,
    mockStartStream: startStream,
    mockGetAgentRoles: getAgentRoles,
  };
});

vi.mock('../../hooks/useIsMobile', () => ({
  useIsMobile: () => true,
  useIsPhone: () => true,
}));

vi.mock('../../api/command', () => ({
  commandApi: {
    getAgentRoles: () => mockGetAgentRoles(),
  },
}));

vi.mock('../../stores', () => {
  const useAgentChatStore = () => ({ ...mockChatState });
  useAgentChatStore.getState = () => ({
    ...mockChatState,
    startNewChat: mockStartNewChat,
    startStream: mockStartStream,
  });
  return { useAgentChatStore };
});

// 对话本体由 ChatPage 渲染（另一个 agent 正在重做它的视觉），这里只验证
// /meeting 就地挂上它、并且没有把用户再跳走。
vi.mock('../ChatPage', () => ({
  default: () => <div data-testid="chat-page">对话本体</div>,
}));

const STORAGE_KEY = 'dsa:research-sessions:v1:user:7';

const researchRecord = (overrides: Partial<Record<string, string>> = {}) => ({
  sessionId: 'research-9',
  topic: '宁德时代 2026 年储能业务怎么看',
  stock: '300750',
  stockName: '宁德时代',
  cycle: '中线',
  at: '2026-11-13T10:00:00.000Z',
  ...overrides,
});

const renderMeeting = (path: string) => {
  const router = createMemoryRouter(
    [{ path: '/meeting', element: <InvestmentMeetingPage /> }],
    { initialEntries: [path] },
  );
  render(<RouterProvider router={router} />);
  return router;
};

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  mockChatState.loading = false;
  mockChatState.progressSteps = [];
  mockChatState.messages = [];
  mockChatState.messagesLoading = false;
  mockChatState.historyError = null;
  mockChatState.chatError = null;
  mockChatState.ownerScope = 'user:7';
  mockChatState.sessionId = 'store-session-1';
  mockGetAgentRoles.mockResolvedValue({
    general: { name: '主席', title: '统筹 / 决策' },
    research: { name: '研究员', title: '研究 / 报告' },
  });
});

afterEach(cleanup);

describe('InvestmentMeetingPage mobile: the single merged conversation entry', () => {
  it('renders the conversation in place instead of a settings page that jumps to /chat', async () => {
    const router = renderMeeting('/meeting');

    expect(await screen.findByTestId('chat-page')).toBeInTheDocument();
    expect(screen.getByTestId('meeting-chat-page')).toBeInTheDocument();
    // 旧设置页的「开始研投会议」跳转按钮不再存在——用户就在对话里。
    expect(screen.queryByRole('button', { name: '开始研投会议' })).not.toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/meeting');
  });

  it('keeps the team agent visible as one compact role strip', async () => {
    renderMeeting('/meeting');

    expect(await screen.findByText('主席')).toBeInTheDocument();
    expect(screen.getByText('研究员')).toBeInTheDocument();
    const group = screen.getByRole('group', { name: 'AI 研究小组角色' });
    expect(group).toHaveTextContent('AI 研究小组');
  });

  it('still lets the user talk when the team roles cannot be read', async () => {
    mockGetAgentRoles.mockResolvedValueOnce({});
    renderMeeting('/meeting');

    expect(await screen.findByText('暂时读不到团队角色，可以直接提问')).toBeInTheDocument();
    expect(screen.getByTestId('chat-page')).toBeInTheDocument();
  });

  it('shows the research topic as the conversation title when ChatPage does not own it', async () => {
    renderMeeting('/meeting?topic=%E5%82%A8%E8%83%BD%E6%80%8E%E4%B9%88%E7%9C%8B');

    const title = await screen.findByTestId('meeting-chat-topic');
    expect(title).toHaveTextContent('研究议题');
    expect(title).toHaveTextContent('储能怎么看');
  });

  it('does not duplicate the topic title when ChatPage already renders it from from=meeting&topic', async () => {
    const topic = encodeURIComponent('宁德时代 2026 年储能业务怎么看');
    const router = renderMeeting(`/meeting?from=meeting&topic=${topic}&session=store-session-1&stock=300750&name=%E5%AE%81%E5%BE%B7%E6%97%B6%E4%BB%A3`);

    expect(await screen.findByTestId('chat-page')).toBeInTheDocument();
    expect(screen.queryByTestId('meeting-chat-topic')).not.toBeInTheDocument();
    // 契约参数原样留在 URL 上，交给 ChatPage 的 researchContext 使用。
    expect(router.state.location.search).toContain('from=meeting');
    expect(router.state.location.search).toContain('session=store-session-1');
    expect(new URLSearchParams(router.state.location.search).get('topic')).toBe('宁德时代 2026 年储能业务怎么看');
  });

  it('starts a research whose first message is the topic, keeping the old topic=session-title semantics', async () => {
    const topic = encodeURIComponent('宁德时代 中线怎么看');
    const router = renderMeeting(`/meeting?topic=${topic}&stock=300750&name=%E5%AE%81%E5%BE%B7%E6%97%B6%E4%BB%A3`);

    fireEvent.click(await screen.findByRole('button', { name: '修改研究议题' }));
    fireEvent.change(screen.getByRole('textbox', { name: '研究议题' }), { target: { value: '宁德时代 中线怎么看' } });
    fireEvent.click(screen.getByRole('button', { name: '开始研究' }));

    await waitFor(() => expect(mockStartStream).toHaveBeenCalledTimes(1));
    expect(mockStartNewChat).toHaveBeenCalledTimes(1);
    expect(mockStartStream).toHaveBeenCalledWith({
      message: '宁德时代 中线怎么看',
      context: { stock_code: '300750', stock_name: '宁德时代' },
    });
    await waitFor(() => expect(router.state.location.search).toContain('session=research-new-1'));
    const params = new URLSearchParams(router.state.location.search);
    expect(params.get('from')).toBe('meeting');
    expect(params.get('topic')).toBe('宁德时代 中线怎么看');
    expect(params.get('stock')).toBe('300750');
  });

  it('refuses to start a research without a topic instead of silently jumping away', async () => {
    renderMeeting('/meeting');

    fireEvent.click(await screen.findByRole('button', { name: '设置研究议题' }));
    fireEvent.change(screen.getByRole('textbox', { name: '研究议题' }), { target: { value: '   ' } });
    fireEvent.click(screen.getByRole('button', { name: '开始研究' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('先写清这次想研究什么');
    expect(mockStartNewChat).not.toHaveBeenCalled();
    expect(mockStartStream).not.toHaveBeenCalled();
  });

  it('relabels an existing conversation instead of starting a new session', async () => {
    mockChatState.messages = [{ id: 'm1', role: 'user', content: '光伏出清到哪一步了' }];
    const router = renderMeeting('/meeting');

    fireEvent.click(await screen.findByRole('button', { name: '设置研究议题' }));
    fireEvent.change(screen.getByRole('textbox', { name: '研究议题' }), { target: { value: '光伏出清进度' } });
    fireEvent.click(screen.getByRole('button', { name: '保存议题' }));

    await waitFor(() => expect(new URLSearchParams(router.state.location.search).get('topic')).toBe('光伏出清进度'));
    expect(mockStartNewChat).not.toHaveBeenCalled();
    expect(mockStartStream).not.toHaveBeenCalled();
  });

  it('passes stock context from old /chat links through to the conversation', async () => {
    const router = renderMeeting('/meeting?stock=300750&name=%E5%AE%81%E5%BE%B7%E6%97%B6%E4%BB%A3');

    expect(await screen.findByTestId('chat-page')).toBeInTheDocument();
    const params = new URLSearchParams(router.state.location.search);
    expect(params.get('stock')).toBe('300750');
    expect(params.get('name')).toBe('宁德时代');
  });

  it('restores the latest research topic when the bottom nav returns without params', async () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([researchRecord({ sessionId: 'store-session-1' })]));

    renderMeeting('/meeting');

    expect(await screen.findByTestId('chat-page')).toBeInTheDocument();
    // 本机研究记录命中当前会话 → ChatPage 会自己展示议题，顶部条不再重复。
    await waitFor(() => expect(screen.queryByTestId('meeting-chat-topic')).not.toBeInTheDocument());
  });
});
