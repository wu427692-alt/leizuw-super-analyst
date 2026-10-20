import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import * as AuthContext from './contexts/AuthContext';
import * as UserAccessContext from './contexts/UserAccessContext';
import { UI_LANGUAGE_STORAGE_KEY } from './utils/uiLanguage';
import { cachedQuery, resetRequestCacheForTests } from './api/requestCache';

type AuthState = ReturnType<typeof AuthContext.useAuth>;
type UserAccessState = ReturnType<typeof UserAccessContext.useUserAccess>;

const { chatPageShouldThrow, setCurrentRoute, useAgentChatStoreMock, privatePageRead, privatePageMount, privatePageUnmount } = vi.hoisted(() => {
  const setCurrentRoute = vi.fn();
  const chatPageShouldThrow = { value: false };
  const state = { completionBadge: false };
  const useAgentChatStoreMock = Object.assign(
    vi.fn((selector?: (value: typeof state) => unknown) => (selector ? selector(state) : state)),
    { getState: () => ({ setCurrentRoute }) },
  );
  return { chatPageShouldThrow, setCurrentRoute, useAgentChatStoreMock,
    privatePageRead: vi.fn<(owner: number) => Promise<string>>(), privatePageMount: vi.fn(), privatePageUnmount: vi.fn() };
});

vi.mock('./contexts/AuthContext', () => ({
  AuthProvider: ({ children }: { children: ReactNode }) => children,
  useAuth: vi.fn(),
}));

vi.mock('./contexts/UserAccessContext', () => ({
  UserAccessProvider: ({ children }: { children: ReactNode }) => children,
  useUserAccess: vi.fn(),
}));

vi.mock('./stores/agentChatStore', () => ({
  useAgentChatStore: useAgentChatStoreMock,
}));

vi.mock('./components/admin', () => ({
  AdminShell: ({ children }: { children: ReactNode }) => <div data-testid="admin-shell">{children}</div>,
}));

vi.mock('./pages/MarketDashboardPage', () => ({
  default: () => <div data-testid="home-page">Home</div>,
}));

vi.mock('./pages/LandingPage', () => ({
  default: () => <div data-testid="landing-page">Landing</div>,
}));

vi.mock('./pages/UserGuidePage', () => ({
  default: () => <div data-testid="guide-page">Guide</div>,
}));

vi.mock('./pages/SuperWatchlistPage', async () => {
  const { useEffect, useState } = await import('react');
  const { useUserAccess } = await import('./contexts/UserAccessContext');
  const { cachedQuery } = await import('./api/requestCache');
  return { default: function PrivatePageProbe() {
    const { user } = useUserAccess();
    const [value, setValue] = useState('正在读取个人资料');
    const [draft, setDraft] = useState('');
    const owner = user!.id;
    useEffect(() => {
      privatePageMount(owner);
      // Deliberately no page-local owner key: the application boundary must
      // provide isolation for all legacy pages, including late state setters.
      void cachedQuery('app-test:private', () => privatePageRead(owner), { freshMs: 60_000 }).then(setValue);
      return () => { privatePageUnmount(owner); };
    }, []); // eslint-disable-line react-hooks/exhaustive-deps
    return <div data-testid="private-page"><p>{value}</p><input aria-label="个人页面草稿" value={draft} onChange={event => setDraft(event.target.value)} /><button onClick={() => {
      void cachedQuery('app-test:private', () => privatePageRead(owner), { freshMs: 60_000, force: true }).then(setValue);
    }}>刷新个人资料</button></div>;
  } };
});

vi.mock('./pages/ChatPage', () => ({
  default: () => {
    if (chatPageShouldThrow.value) {
      throw new Error('chunk load failed');
    }
    return <div data-testid="chat-page">Chat</div>;
  },
}));

vi.mock('./pages/TokenUsagePage', () => ({
  default: () => <div data-testid="token-usage-page">Usage</div>,
}));

vi.mock('./pages/SettingsPage', () => ({
  default: () => <div data-testid="settings-page">Settings</div>,
}));

vi.mock('./pages/NotFoundPage', () => ({
  default: () => <div data-testid="not-found-page">Not Found</div>,
}));

vi.mock('./pages/LoginPage', () => ({
  default: () => <div data-testid="login-page">Login</div>,
}));

vi.mock('./pages/UserAccessPage', () => ({
  default: () => <div data-testid="user-access-page">User access</div>,
}));

vi.mock('./pages/AdminConsolePage', () => ({
  default: () => <div data-testid="admin-console-page">Admin console</div>,
}));

function makeAuthState(overrides: Partial<AuthState> = {}): AuthState {
  return {
    authEnabled: false,
    loggedIn: false,
    passwordSet: false,
    passwordChangeable: false,
    setupState: 'no_password',
    isLoading: false,
    loadError: null,
    login: vi.fn().mockResolvedValue({ success: true }),
    changePassword: vi.fn().mockResolvedValue({ success: true }),
    logout: vi.fn().mockResolvedValue(undefined),
    refreshStatus: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

function makeUserAccessState(overrides: Partial<UserAccessState> = {}): UserAccessState {
  return {
    accessEnabled: true,
    loggedIn: true,
    user: { id: 1, name: '测试用户', status: 'approved' },
    authMethod: 'session',
    isLoading: false,
    registrationMode: 'approval',
    smsLoginEnabled: false,
    register: vi.fn().mockResolvedValue({ success: true, pending: true }),
    login: vi.fn().mockResolvedValue({ success: true }),
    sendSms: vi.fn().mockResolvedValue({ success: false }),
    smsLogin: vi.fn().mockResolvedValue({ success: false }),
    bindPhone: vi.fn().mockResolvedValue({ success: false }),
    logout: vi.fn().mockResolvedValue(undefined),
    refresh: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  resetRequestCacheForTests();
  privatePageRead.mockReset();
  privatePageRead.mockImplementation(async owner => `账号 ${owner} 的个人资料`);
  chatPageShouldThrow.value = false;
  window.history.pushState({}, '', '/');
  localStorage.setItem(UI_LANGUAGE_STORAGE_KEY, 'zh');
  vi.mocked(AuthContext.useAuth).mockReturnValue(makeAuthState());
  vi.mocked(UserAccessContext.useUserAccess).mockReturnValue(makeUserAccessState());
});

describe('App routing behavior', () => {
  it('shows the introduction at the public root and keeps the dashboard at /app', async () => {
    render(<App />);

    expect(await screen.findByTestId('landing-page')).toBeInTheDocument();
    expect(screen.queryByTestId('home-page')).not.toBeInTheDocument();
  });

  it('renders the market dashboard inside the application route', async () => {
    window.history.pushState({}, '', '/app');

    render(<App />);

    expect(await screen.findByTestId('home-page')).toBeInTheDocument();
    expect(screen.queryByTestId('landing-page')).not.toBeInTheDocument();
  });

  it('redirects an unauthenticated direct application visit to user access', async () => {
    vi.mocked(UserAccessContext.useUserAccess).mockReturnValue(makeUserAccessState({
      loggedIn: false,
      user: null,
      authMethod: null,
    }));
    window.history.pushState({}, '', '/essay-radar/feed?keyword=光模块');

    render(<App />);

    expect(await screen.findByTestId('user-access-page')).toBeInTheDocument();
    expect(window.location.pathname).toBe('/access');
    expect(new URLSearchParams(window.location.search).get('redirect')).toBe('/essay-radar/feed?keyword=%E5%85%89%E6%A8%A1%E5%9D%97');
    expect(screen.queryByTestId('landing-page')).not.toBeInTheDocument();
  });

  it('allows an authenticated user to open phone binding without a redirect loop', async () => {
    window.history.pushState({}, '', '/access?bind=phone&redirect=%2Fapp');
    render(<App />);
    expect(await screen.findByTestId('user-access-page')).toBeVisible();
    expect(window.location.pathname).toBe('/access');
  });

  it('rejects a protocol-relative post-login destination', async () => {
    window.history.pushState({}, '', '/access?redirect=%2F%2Fevil.invalid');
    render(<App />);
    expect(await screen.findByTestId('home-page')).toBeVisible();
    expect(window.location.pathname).toBe('/app');
  });

  it('shows loading fallback while an admin route is initializing auth', () => {
    vi.mocked(AuthContext.useAuth).mockReturnValue(makeAuthState({ isLoading: true }));
    window.history.pushState({}, '', '/admin');

    render(<App />);

    expect(screen.getByRole('status', { name: '正在确认管理员权限' })).toBeInTheDocument();
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    expect(screen.queryByTestId('admin-shell')).not.toBeInTheDocument();
    expect(screen.queryByTestId('admin-console-page')).not.toBeInTheDocument();
  });

  it.each(['/super-watchlist', '/tasks', '/essay-radar/feed'])('keeps the complete authorization gate while %s is waiting for identity', async path => {
    vi.mocked(UserAccessContext.useUserAccess).mockReturnValue(makeUserAccessState({ isLoading: true }));
    window.history.pushState({}, '', path);
    const view = render(<App />);
    expect(screen.getByRole('status', { name: '正在确认访问权限' })).toBeVisible();
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    expect(screen.queryByRole('navigation', { name: '主导航' })).not.toBeInTheDocument();
    expect(privatePageMount).not.toHaveBeenCalled();

    vi.mocked(UserAccessContext.useUserAccess).mockReturnValue(makeUserAccessState({ loggedIn: false, user: null, authMethod: null }));
    view.rerender(<App />);
    expect(await screen.findByTestId('user-access-page')).toBeVisible();
    expect(privatePageMount).not.toHaveBeenCalled();
    expect(screen.queryByRole('status', { name: '正在确认访问权限' })).not.toBeInTheDocument();
  });

  it('mounts the private route only after the identity check completes successfully', async () => {
    vi.mocked(UserAccessContext.useUserAccess).mockReturnValue(makeUserAccessState({ isLoading: true }));
    window.history.pushState({}, '', '/super-watchlist');
    const view = render(<App />);
    expect(privatePageMount).not.toHaveBeenCalled();
    vi.mocked(UserAccessContext.useUserAccess).mockReturnValue(makeUserAccessState());
    view.rerender(<App />);
    expect(await screen.findByTestId('private-page')).toBeVisible();
    expect(privatePageMount).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('status', { name: '正在确认访问权限' })).not.toBeInTheDocument();
  });

  it.each(['/portfolio', '/decision-signals', '/backtest', '/alerts', '/jev-backtest', '/jev-backtest/task'])(
    'redirects the retired public route %s to the dashboard',
    async (retiredRoute) => {
    vi.mocked(AuthContext.useAuth).mockReturnValue(makeAuthState({
      authEnabled: true,
      loggedIn: false,
      setupState: 'enabled',
    }));
    window.history.pushState({}, '', retiredRoute);

    render(<App />);

    expect(await screen.findByTestId('home-page')).toBeInTheDocument();
    expect(window.location.pathname).toBe('/app');
    expect(screen.queryByTestId('login-page')).not.toBeInTheDocument();
    },
  );

  it('redirects protected admin routes to the dedicated admin login', async () => {
    vi.mocked(AuthContext.useAuth).mockReturnValue(makeAuthState({
      authEnabled: true,
      loggedIn: false,
      setupState: 'enabled',
    }));
    window.history.pushState({}, '', '/admin/settings');

    render(<App />);

    expect(await screen.findByTestId('login-page')).toBeInTheDocument();
    expect(window.location.pathname).toBe('/admin/login');
    expect(window.location.search).toBe('?redirect=%2Fadmin%2Fsettings');
  });

  it('renders the current route page after auth is ready', async () => {
    window.history.pushState({}, '', '/chat');

    render(<App />);

    expect(await screen.findByTestId('chat-page')).toBeInTheDocument();
    expect(setCurrentRoute).toHaveBeenCalledWith('/chat');
    expect(screen.queryByTestId('login-page')).not.toBeInTheDocument();
    expect(screen.queryByTestId('home-page')).not.toBeInTheDocument();
  });

  it('routes /admin/usage to the token usage page after auth is ready', async () => {
    vi.mocked(AuthContext.useAuth).mockReturnValue(makeAuthState({ authEnabled: true, loggedIn: true, setupState: 'enabled' }));
    window.history.pushState({}, '', '/admin/usage');

    render(<App />);

    expect(await screen.findByTestId('token-usage-page')).toBeInTheDocument();
    expect(setCurrentRoute).toHaveBeenCalledWith('/admin/usage');
    expect(screen.queryByTestId('home-page')).not.toBeInTheDocument();
  });

  it('redirects authenticated admin login visits to the admin console', async () => {
    vi.mocked(AuthContext.useAuth).mockReturnValue(makeAuthState({
      authEnabled: true,
      loggedIn: true,
      setupState: 'enabled',
    }));
    window.history.pushState({}, '', '/admin/login');

    render(<App />);

    expect(await screen.findByTestId('admin-console-page')).toBeInTheDocument();
    expect(screen.queryByTestId('login-page')).not.toBeInTheDocument();
  });

  it('keeps the shell mounted and resets the route boundary after page render errors', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    chatPageShouldThrow.value = true;
    window.history.pushState({}, '', '/chat');

    try {
      render(<App />);

      expect(await screen.findByRole('heading', { name: '模块暂未完成加载' })).toBeInTheDocument();
      expect(screen.getByRole('navigation', { name: '主导航' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '重试当前模块' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '返回首页' })).toBeInTheDocument();

      chatPageShouldThrow.value = false;
      fireEvent.click(screen.getAllByRole('link', { name: '市场总览' })[0]);

      expect(await screen.findByTestId('home-page')).toBeInTheDocument();
      expect(screen.queryByRole('heading', { name: '模块暂未完成加载' })).not.toBeInTheDocument();
    } finally {
      consoleError.mockRestore();
    }
  });

  it('remounts private route state before exposing a different logged-in owner and rejects late cached data', async () => {
    let finishOld!: (value: string) => void;
    let finishNew!: (value: string) => void;
    privatePageRead.mockResolvedValueOnce('账号 A 已读取的资料')
      .mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve; }))
      .mockImplementationOnce(() => new Promise(resolve => { finishNew = resolve; }));
    window.history.pushState({}, '', '/super-watchlist');
    const view = render(<App />);
    expect(await screen.findByText('账号 A 已读取的资料')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: '个人页面草稿' }), { target: { value: '账号 A 的未提交内容' } });
    fireEvent.click(screen.getByRole('button', { name: '刷新个人资料' }));
    await waitFor(() => expect(privatePageRead).toHaveBeenCalledTimes(2));
    vi.mocked(UserAccessContext.useUserAccess).mockReturnValue(makeUserAccessState({ user: { id: 2, name: '账号 B', status: 'approved' } }));
    view.rerender(<App />);
    expect(screen.queryByText('账号 A 已读取的资料')).not.toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: '个人页面草稿' })).toHaveValue('');
    expect(privatePageUnmount).toHaveBeenCalledWith(1);
    expect(privatePageRead).toHaveBeenLastCalledWith(2);
    await act(async () => { finishOld('账号 A 的迟到资料'); });
    expect(screen.queryByText('账号 A 的迟到资料')).not.toBeInTheDocument();
    await act(async () => { finishNew('账号 B 的资料'); });
    expect(screen.getByText('账号 B 的资料')).toBeInTheDocument();
    const unexpectedRead = vi.fn(async () => '错误缓存');
    expect(await cachedQuery('app-test:private', unexpectedRead, { freshMs: 60_000 })).toBe('账号 B 的资料');
    expect(unexpectedRead).not.toHaveBeenCalled();
  });

  it('does not remount a private page on an ordinary refresh of the same identity', async () => {
    window.history.pushState({}, '', '/super-watchlist');
    const view = render(<App />);
    expect(await screen.findByText('账号 1 的个人资料')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: '个人页面草稿' }), { target: { value: '保持草稿' } });
    vi.mocked(UserAccessContext.useUserAccess).mockReturnValue(makeUserAccessState({ user: { id: 1, name: '更新显示名', status: 'approved' } }));
    view.rerender(<App />);
    expect(screen.getByRole('textbox', { name: '个人页面草稿' })).toHaveValue('保持草稿');
    expect(privatePageMount).toHaveBeenCalledTimes(1);
    expect(privatePageUnmount).not.toHaveBeenCalled();
    expect(privatePageRead).toHaveBeenCalledTimes(1);
  });

  it.each([
    { name: 'approval', overrides: { user: { id: 1, name: '测试用户', status: 'pending' as const } } },
    { name: 'authentication method', overrides: { authMethod: 'password' } },
    { name: 'access policy', overrides: { accessEnabled: false } },
  ])('resets private state when $name changes', async ({ overrides }) => {
    window.history.pushState({}, '', '/super-watchlist');
    const view = render(<App />);
    await screen.findByTestId('private-page');
    fireEvent.change(screen.getByRole('textbox', { name: '个人页面草稿' }), { target: { value: '旧权限内容' } });
    vi.mocked(UserAccessContext.useUserAccess).mockReturnValue(makeUserAccessState(overrides));
    view.rerender(<App />);
    expect(screen.getByRole('textbox', { name: '个人页面草稿' })).toHaveValue('');
    expect(privatePageUnmount).toHaveBeenCalledTimes(1);
  });

  it.each([
    { route: '/', testId: 'landing-page' },
    { route: '/guide', testId: 'guide-page' },
    { route: '/admin', testId: 'admin-console-page' },
  ])('does not remount independent public or administrator route $route on a front-user change', async ({ route, testId }) => {
    vi.mocked(AuthContext.useAuth).mockReturnValue(makeAuthState({ authEnabled: true, loggedIn: true, setupState: 'enabled' }));
    window.history.pushState({}, '', route);
    const view = render(<App />);
    const original = await screen.findByTestId(testId);
    vi.mocked(UserAccessContext.useUserAccess).mockReturnValue(makeUserAccessState({ user: { id: 2, name: '账号 B', status: 'approved' } }));
    view.rerender(<App />);
    expect(screen.getByTestId(testId)).toBe(original);
    expect(screen.queryByTestId('private-page')).not.toBeInTheDocument();
  });
});
