import { fireEvent, render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import * as AuthContext from './contexts/AuthContext';
import * as UserAccessContext from './contexts/UserAccessContext';
import { UI_LANGUAGE_STORAGE_KEY } from './utils/uiLanguage';

type AuthState = ReturnType<typeof AuthContext.useAuth>;
type UserAccessState = ReturnType<typeof UserAccessContext.useUserAccess>;

const { chatPageShouldThrow, setCurrentRoute, useAgentChatStoreMock } = vi.hoisted(() => {
  const setCurrentRoute = vi.fn();
  const chatPageShouldThrow = { value: false };
  const state = { completionBadge: false };
  const useAgentChatStoreMock = Object.assign(
    vi.fn((selector?: (value: typeof state) => unknown) => (selector ? selector(state) : state)),
    { getState: () => ({ setCurrentRoute }) },
  );
  return { chatPageShouldThrow, setCurrentRoute, useAgentChatStoreMock };
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

vi.mock('./pages/ChatPage', () => ({
  default: () => {
    if (chatPageShouldThrow.value) {
      throw new Error('chunk load failed');
    }
    return <div data-testid="chat-page">Chat</div>;
  },
}));

vi.mock('./pages/PortfolioPage', () => ({
  default: () => <div data-testid="portfolio-page">Portfolio</div>,
}));

vi.mock('./pages/DecisionSignalsPage', () => ({
  default: () => <div data-testid="decision-signals-page">Decision signals</div>,
}));

vi.mock('./pages/BacktestPage', () => ({
  default: () => <div data-testid="backtest-page">Backtest</div>,
}));

vi.mock('./pages/AlertsPage', () => ({
  default: () => <div data-testid="alerts-page">Alerts</div>,
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
    register: vi.fn().mockResolvedValue({ success: true, pending: true }),
    login: vi.fn().mockResolvedValue({ success: true }),
    logout: vi.fn().mockResolvedValue(undefined),
    refresh: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
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

  it('shows loading fallback while an admin route is initializing auth', () => {
    vi.mocked(AuthContext.useAuth).mockReturnValue(makeAuthState({ isLoading: true }));
    window.history.pushState({}, '', '/admin');

    render(<App />);

    expect(screen.getByRole('progressbar', { name: '页面加载进度' })).toBeInTheDocument();
  });

  it('keeps public routes available when admin auth is enabled but user is not logged in', async () => {
    vi.mocked(AuthContext.useAuth).mockReturnValue(makeAuthState({
      authEnabled: true,
      loggedIn: false,
      setupState: 'enabled',
    }));
    window.history.pushState({}, '', '/portfolio');

    render(<App />);

    expect(await screen.findByTestId('portfolio-page')).toBeInTheDocument();
    expect(window.location.pathname).toBe('/portfolio');
    expect(screen.queryByTestId('login-page')).not.toBeInTheDocument();
  });

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

  it('routes /decision-signals to the AI signals page after auth is ready', async () => {
    window.history.pushState({}, '', '/decision-signals');

    render(<App />);

    expect(await screen.findByTestId('decision-signals-page')).toBeInTheDocument();
    expect(setCurrentRoute).toHaveBeenCalledWith('/decision-signals');
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
      fireEvent.click(screen.getByRole('link', { name: '持仓' }));

      expect(await screen.findByTestId('portfolio-page')).toBeInTheDocument();
      expect(screen.queryByRole('heading', { name: '模块暂未完成加载' })).not.toBeInTheDocument();
    } finally {
      consoleError.mockRestore();
    }
  });
});
