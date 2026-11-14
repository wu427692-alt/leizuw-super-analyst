/**
 * 合并入口的合成冒烟：/meeting 手机端「团队角色条 + 真实 ChatPage」必须能一起挂起来。
 * `.mobile.test.tsx` 里 ChatPage 被 stub 掉（只测本页逻辑），这里补一条真实组件
 * 的挂载验证，避免"两个都对、拼起来报错"这类问题只在人工点页面时才暴露。
 */
import { beforeAll, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import InvestmentMeetingPage from '../InvestmentMeetingPage';

const mockStoreState = {
  messages: [] as unknown[],
  loading: false,
  messagesLoading: false,
  historyError: null,
  sessionsError: null as unknown,
  progressSteps: [] as unknown[],
  sessionId: 'session-1',
  sessions: [] as unknown[],
  sessionsLoading: false,
  chatError: null as unknown,
  ownerScope: 'user:7',
  currentRoute: '/meeting',
  completionBadge: false,
  hasInitialLoad: true,
  abortController: null,
  loadSessions: vi.fn().mockResolvedValue(undefined),
  loadInitialSession: vi.fn().mockResolvedValue(undefined),
  switchSession: vi.fn().mockResolvedValue(undefined),
  startStream: vi.fn().mockResolvedValue(undefined),
  clearCompletionBadge: vi.fn(),
  retrySession: vi.fn().mockResolvedValue(undefined),
  stopStream: vi.fn(),
  startNewChat: vi.fn(),
  setOwnerScope: vi.fn(),
};

vi.mock('../../hooks/useIsMobile', () => ({
  useIsMobile: () => true,
  useIsPhone: () => true,
}));

vi.mock('../../contexts/UserAccessContext', () => ({
  useUserAccess: () => ({ accessEnabled: false, loggedIn: true, user: { id: 1, name: '测试用户', status: 'approved' }, authMethod: 'local' }),
}));

vi.mock('../../api/command', () => ({
  commandApi: { getAgentRoles: () => Promise.resolve({ general: { name: '主席', title: '统筹 / 决策' } }) },
}));

vi.mock('../../api/agent', () => ({
  agentApi: {
    getSkills: vi.fn().mockResolvedValue({ skills: [], default_skill_id: '' }),
    deleteChatSession: vi.fn().mockResolvedValue(undefined),
    sendChat: vi.fn().mockResolvedValue({ success: true }),
    getChatSessions: vi.fn().mockResolvedValue([]),
    getChatSessionMessages: vi.fn().mockResolvedValue([]),
  },
}));

vi.mock('../../api/systemConfig', () => ({
  systemConfigApi: {
    getConfig: vi.fn().mockResolvedValue({ configVersion: 'v1', maskToken: '', items: [] }),
    update: vi.fn().mockResolvedValue({}),
    getWatchlist: vi.fn().mockResolvedValue([]),
    addToWatchlist: vi.fn().mockResolvedValue({}),
    removeFromWatchlist: vi.fn().mockResolvedValue({}),
  },
}));

vi.mock('../../utils/chatExport', () => ({
  downloadSession: vi.fn(),
  formatSessionAsMarkdown: vi.fn(),
}));

vi.mock('../../api/history', () => ({ historyApi: { getDetail: vi.fn().mockResolvedValue({}) } }));

vi.mock('../../stores/agentChatStore', () => {
  const useAgentChatStore = (selector?: (state: typeof mockStoreState) => unknown) => (
    typeof selector === 'function' ? selector(mockStoreState) : mockStoreState
  );
  useAgentChatStore.getState = () => ({ ...mockStoreState });
  return { useAgentChatStore };
});

beforeAll(() => {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false, media: query, onchange: null,
      addListener: vi.fn(), removeListener: vi.fn(),
      addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
    })),
  });
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { writable: true, value: vi.fn() });
});

describe('meeting page composition smoke', () => {
  it('mounts the real ChatPage under the team strip', async () => {
    const router = createMemoryRouter([{ path: '/meeting', element: <InvestmentMeetingPage /> }], {
      initialEntries: ['/meeting'],
    });
    render(<RouterProvider router={router} />);

    expect(await screen.findByTestId('meeting-chat-page')).toBeInTheDocument();
    expect(screen.getByTestId('chat-workspace')).toBeInTheDocument();
    expect(await screen.findByText('主席')).toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'AI 研究小组角色' })).toBeInTheDocument();
  });
});
