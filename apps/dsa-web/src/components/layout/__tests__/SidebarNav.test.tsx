import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { SidebarNav } from '../SidebarNav';
import { UiLanguageProvider } from '../../../contexts/UiLanguageContext';
import { UI_LANGUAGE_STORAGE_KEY } from '../../../utils/uiLanguage';

const mockLogout = vi.fn().mockResolvedValue(undefined);
const mockGetAlphaSiftStatus = vi.fn().mockResolvedValue({ enabled: false, available: false, installSpecIsDefault: false });
const mockThemeToggle = vi.fn(({ collapsed }: { collapsed?: boolean }) => (
  <button type="button">{collapsed ? '切换主题(折叠)' : '切换主题'}</button>
));

const completionBadgeState = { value: true };

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({
    authEnabled: true,
    logout: mockLogout,
  }),
}));

vi.mock('../../../stores/agentChatStore', () => ({
  useAgentChatStore: (selector: (state: { completionBadge: boolean }) => unknown) =>
    selector({ completionBadge: completionBadgeState.value }),
}));

vi.mock('../../../api/alphasift', () => ({
  ALPHASIFT_CONFIG_CHANGED_EVENT: 'alphasift-config-changed',
  SYSTEM_CONFIG_CHANGED_EVENT: 'dsa-system-config-changed',
  alphasiftApi: {
    getStatus: () => mockGetAlphaSiftStatus(),
  },
}));

vi.mock('../../theme/ThemeToggle', () => ({
  ThemeToggle: (props: { collapsed?: boolean }) => mockThemeToggle(props),
}));

describe('SidebarNav', () => {
  it.each([
    ['zh', ['市场总览', '题材图谱', '自选看板', '深度研究', '任务中心', 'AI 热点', '机构纪要与录音', '量化研究', '数据下载', 'AI 问股', '使用指南']],
    ['en', ['Market Overview', 'Theme Map', 'Watchlist', 'Deep Research', 'Task Center', 'AI News', 'Institutional Notes & Audio', 'Quant Research', 'Data Downloads', 'AI Stock Q&A', 'User Guide']],
  ] as const)('uses concise and complete %s workspace names in the expanded and collapsed sidebar', (language, labels) => {
    const previousLanguage = localStorage.getItem(UI_LANGUAGE_STORAGE_KEY);
    localStorage.setItem(UI_LANGUAGE_STORAGE_KEY, language);
    try {
      const { rerender } = render(
        <UiLanguageProvider><MemoryRouter><SidebarNav documentNavigation /></MemoryRouter></UiLanguageProvider>,
      );
      for (const label of labels) expect(screen.getByRole('link', { name: label })).toHaveTextContent(label);

      rerender(<UiLanguageProvider><MemoryRouter><SidebarNav documentNavigation collapsed /></MemoryRouter></UiLanguageProvider>);
      for (const label of labels) {
        const link = screen.getByRole('link', { name: label });
        expect(link.textContent).toBe('');
      }
      expect(screen.queryByRole('link', { name: /Jev/i })).not.toBeInTheDocument();
    } finally {
      if (previousLanguage === null) localStorage.removeItem(UI_LANGUAGE_STORAGE_KEY);
      else localStorage.setItem(UI_LANGUAGE_STORAGE_KEY, previousLanguage);
    }
  });

  it('reuses the shared navigation with native document links and no unrelated status reads', () => {
    mockGetAlphaSiftStatus.mockClear();
    render(<MemoryRouter initialEntries={['/ai-news']}><SidebarNav documentNavigation /></MemoryRouter>);
    expect(mockGetAlphaSiftStatus).not.toHaveBeenCalled();
    expect(screen.getByRole('link', { name: 'AI 热点' })).toHaveAttribute('aria-current', 'page');
    for (const label of ['市场总览', '自选看板', '管理员']) {
      const click = new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 });
      screen.getByRole('link', { name: label }).dispatchEvent(click);
      expect(click.defaultPrevented).toBe(false);
    }
  });
  it('hides the screening navigation item while AlphaSift is disabled', () => {
    mockGetAlphaSiftStatus.mockResolvedValueOnce({ enabled: false, available: false, installSpecIsDefault: false });

    render(
      <MemoryRouter initialEntries={['/']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    expect(screen.queryByRole('link', { name: '智能选股' })).not.toBeInTheDocument();
  });

  it('shows the screening navigation item when AlphaSift is enabled', async () => {
    mockGetAlphaSiftStatus.mockResolvedValueOnce({ enabled: true, available: false, installSpecIsDefault: false });

    render(
      <MemoryRouter initialEntries={['/']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('link', { name: '智能选股' })).toHaveAttribute('href', '/screening');
  });

  it('keeps five decision workspaces before research tools', async () => {
    mockGetAlphaSiftStatus.mockResolvedValueOnce({ enabled: true, available: false, installSpecIsDefault: false });

    render(
      <MemoryRouter initialEntries={['/']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    await screen.findByRole('link', { name: '智能选股' });
    const hrefs = screen.getAllByRole('link').map((link) => link.getAttribute('href'));
    expect(hrefs.slice(0, 13)).toEqual([
      '/app',
      '/concept-themes',
      '/super-watchlist',
      '/industry-research',
      '/tasks',
      '/ai-news',
      '/essay-radar',
      '/essay-quant',
      '/data-acquisition',
      '/chat',
      '/screening',
      '/guide',
      '/admin',
    ]);
  });

  it('refreshes the screening navigation item after any config save event', async () => {
    mockGetAlphaSiftStatus
      .mockResolvedValueOnce({ enabled: false, available: false, installSpecIsDefault: false })
      .mockResolvedValueOnce({ enabled: true, available: false, installSpecIsDefault: false });

    render(
      <MemoryRouter initialEntries={['/']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    expect(screen.queryByRole('link', { name: '智能选股' })).not.toBeInTheDocument();
    window.dispatchEvent(new Event('dsa-system-config-changed'));

    expect(await screen.findByRole('link', { name: '智能选股' })).toHaveAttribute('href', '/screening');
    await waitFor(() => expect(mockGetAlphaSiftStatus.mock.calls.length).toBeGreaterThanOrEqual(2));
  });

  it('shows the shared completion badge only when chat completion is pending', () => {
    completionBadgeState.value = true;

    const { rerender } = render(
      <MemoryRouter initialEntries={['/chat']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    expect(screen.getByTestId('chat-completion-badge')).toBeInTheDocument();
    expect(screen.getByLabelText('问股有新消息')).toBeInTheDocument();

    completionBadgeState.value = false;
    rerender(
      <MemoryRouter initialEntries={['/chat']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    expect(screen.queryByTestId('chat-completion-badge')).not.toBeInTheDocument();
  });

  it('renders the collapsed theme toggle variant when the sidebar is collapsed', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <SidebarNav collapsed />
      </MemoryRouter>,
    );

    expect(mockThemeToggle).toHaveBeenCalledWith(
      expect.objectContaining({ variant: 'nav', collapsed: true }),
    );
    expect(screen.getByRole('button', { name: '切换主题(折叠)' })).toBeInTheDocument();
  });

  it('does not expose retired portfolio, AI suggestion, backtest, or alert routes', () => {
    render(
      <MemoryRouter initialEntries={['/app']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    expect(screen.queryByRole('link', { name: '持仓' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'AI 建议' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '回测' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '告警' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Jev/i })).not.toBeInTheDocument();
    expect(document.querySelector('a[href^="/jev-"]')).toBeNull();
  });

  it('keeps logout out of the public navigation and links administration', () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    expect(screen.getByRole('link', { name: '管理员' })).toHaveAttribute('href', '/admin');
    expect(screen.getByRole('link', { name: '管理员' })).toHaveClass('shrink-0');
    expect(screen.getByRole('navigation', { name: '主导航' })).toHaveClass('overflow-y-auto');
    expect(screen.queryByRole('button', { name: '退出' })).not.toBeInTheDocument();
  });
});
