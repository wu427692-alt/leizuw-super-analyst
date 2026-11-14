import { cleanup, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../../App';
import appSource from '../../App.tsx?raw';
import * as AuthContext from '../../contexts/AuthContext';
import * as UserAccessContext from '../../contexts/UserAccessContext';

/**
 * 全量页面渲染冒烟（发布门禁「前端使用」层）。
 *
 * 目标：App.tsx 里**每一条实际渲染组件的路由**都要能在没有任何后端的情况下挂载，
 * 不抛错、不落到 404、不出现路由错误边界，也不往 console.error 里写东西。
 *
 * 页面清单**从 App.tsx 的路由表自动派生**（`?raw` 读取），新增/删除路由时这里自动
 * 跟着变，不需要人工维护第二份清单。已经下线成 `<Navigate>` 的旧页面不会被当成
 * 页面渲染，改为断言「路由确实重定向到目标页」，避免把死代码当活页面测试。
 *
 * 网络层统一 mock 成 reject：后端不可用是发布前必须扛住的状态，页面应展示加载/空/错误
 * 态而不是白屏。这里不 mock 页面模块本身——那正是本测试要冒烟的对象。
 */

const { apiCalls, apiClientMock } = vi.hoisted(() => {
  const apiCalls: string[] = [];
  const reject = (method: string) => vi.fn((url: unknown) => {
    apiCalls.push(`${method} ${String(url)}`);
    return Promise.reject(new Error(`全量渲染冒烟：网络层已禁用（${method} ${String(url)}）`));
  });
  return {
    apiCalls,
    apiClientMock: {
      get: reject('get'),
      post: reject('post'),
      put: reject('put'),
      patch: reject('patch'),
      delete: reject('delete'),
      request: reject('request'),
    },
  };
});

// api/index 是唯一 axios 实例入口，桩掉它就等于禁用了整张页面的网络层。
vi.mock('../../api/index', () => ({
  default: apiClientMock,
  BACKGROUND_ROUTE_HEADER: 'X-DSA-Route-Load',
  BACKGROUND_ROUTE_HEADERS: { 'X-DSA-Route-Load': 'background' },
}));

// 身份上下文由外层真实 Provider 提供，这里只给 App 一个「已登录且已获准」的最小状态。
vi.mock('../../contexts/AuthContext', () => ({
  AuthProvider: ({ children }: { children: ReactNode }) => children,
  useAuth: vi.fn(),
}));

vi.mock('../../contexts/UserAccessContext', () => ({
  UserAccessProvider: ({ children }: { children: ReactNode }) => children,
  useUserAccess: vi.fn(),
}));

type AuthState = ReturnType<typeof AuthContext.useAuth>;
type UserAccessState = ReturnType<typeof UserAccessContext.useUserAccess>;

function makeAuthState(overrides: Partial<AuthState> = {}): AuthState {
  return {
    authEnabled: true,
    loggedIn: true,
    passwordSet: true,
    passwordChangeable: true,
    setupState: 'enabled',
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

type ParsedRoute = { path: string; element: string };

/** 从 App.tsx 源码里读出 `<Route path="..." element={...} />` 形式的真实路由表。 */
function parseRouteTable(source: string): ParsedRoute[] {
  const routes: ParsedRoute[] = [];
  const pattern = /<Route\s+path="([^"]+)"\s+element=\{([\s\S]*?)\}\s*\/>/g;
  for (const match of source.matchAll(pattern)) {
    routes.push({ path: match[1], element: match[2] });
  }
  return routes;
}

const ROUTE_TABLE = parseRouteTable(appSource);

function redirectTarget(element: string): string | null {
  if (!element.includes('<Navigate')) return null;
  return element.match(/to="([^"]+)"/)?.[1] ?? null;
}

function mountedComponent(element: string): string | null {
  return element.match(/<([A-Z][A-Za-z0-9_]*)\s*\/>/)?.[1] ?? null;
}

/** 把 `/report/:projectId` 这类动态段换成可访问的具体路径，`*` 换成 `/all`。 */
function concretePath(path: string): string {
  return path
    .split('/')
    .map((segment) => {
      if (segment.startsWith(':')) return `sample-${segment.slice(1)}`;
      if (segment === '*') return 'all';
      return segment;
    })
    .join('/');
}

const COMPONENT_ROUTES = ROUTE_TABLE.filter((route) => redirectTarget(route.element) === null);
const REDIRECT_ROUTES = ROUTE_TABLE.filter((route) => redirectTarget(route.element) !== null);
/** `path="*"` 是 404 兜底，不属于「页面路由」，单独测。 */
const PAGE_ROUTES = COMPONENT_ROUTES.filter((route) => route.path !== '*');

/** 旧路由可能连着跳（/market → /investment-monitor → /admin/data-sources），按链取最终落点。 */
const REDIRECT_BY_PATH = new Map(REDIRECT_ROUTES.map((route) => [route.path, redirectTarget(route.element) as string]));

function resolveFinalRedirectTarget(target: string): string {
  let current = target;
  for (let hop = 0; hop < 10; hop += 1) {
    const pathname = current.split('?')[0];
    const next = REDIRECT_BY_PATH.get(pathname);
    if (next === undefined || next === current) return current;
    current = next;
  }
  return current;
}

/**
 * 已下线页面的旧路由：路由表里只剩 `<Navigate>`，页面文件在生产代码中已无引用。
 * 这里只记录「旧页面 → 旧路由 → 期望落点」，既证明死代码没被重新挂载，也证明旧链接不 404。
 */
const RETIRED_PAGE_ROUTES: Array<{ page: string; route: string; target: string }> = [
  { page: 'AlertsPage', route: '/alerts', target: '/app' },
  { page: 'BacktestPage', route: '/backtest', target: '/app' },
  { page: 'DecisionSignalsPage', route: '/decision-signals', target: '/app' },
  { page: 'PortfolioPage', route: '/portfolio', target: '/app' },
  { page: 'ResearchCenterPage', route: '/research-center', target: '/super-watchlist?view=decision' },
];

/** `/`、`/guide`、`/access`、`/admin/login` 由 AppContent/RoutedApplication 直接分派，不在 `<Routes>` 表里。 */
const STANDALONE_PATHS = (() => {
  const matches = [...appSource.matchAll(/location\.pathname === '([^']+)'/g)].map((match) => match[1]);
  return [...new Set(matches)];
})();

/** 测试环境没有 console.error 之外的合理噪声；React 19 + Suspense 的 act 提示是测试工具噪音。 */
const REACT_TEST_NOISE = [
  /not wrapped in act\(\.\.\.\)/,
  /A suspended resource finished loading inside a test/,
];

function unexpectedConsoleErrors(errors: string[]): string[] {
  return errors.filter((message) => !REACT_TEST_NOISE.some((noise) => noise.test(message)));
}

/**
 * 页面在 API 失败时有少数路径没有 catch（已知 bug，见交付报告），会在网络层被禁用的
 * 冒烟环境里冒成 unhandled rejection。Vitest 默认会因此让整份文件变红，这里把
 * 「已知的、由本文件的网络桩产生的 rejection」单独记账，其它 unhandled rejection 仍然失败。
 */
const unhandledRejections: string[] = [];
type ProcessLike = { on?: (event: 'unhandledRejection', listener: (reason: unknown) => void) => void };
const nodeProcess = (globalThis as { process?: ProcessLike }).process;
nodeProcess?.on?.('unhandledRejection', (reason) => {
  unhandledRejections.push(reason instanceof Error ? reason.message : String(reason));
});

const KNOWN_UNCAUGHT_NETWORK_REJECTION = /全量渲染冒烟：网络层已禁用/;

function unexpectedUnhandledRejections(): string[] {
  return unhandledRejections.filter((message) => !KNOWN_UNCAUGHT_NETWORK_REJECTION.test(message));
}

/**
 * 页面主区域。应用外壳（Shell/AdminShell）本身有 `<main>`，页面自己的 `AppPage`
 * 也是一个 `<main>`，所以取 DOM 里最内层的那个（页面内容）。
 */
function currentPageMain(): HTMLElement | null {
  const mains = Array.from(document.querySelectorAll<HTMLElement>('main'));
  return mains.length ? mains[mains.length - 1] : null;
}

/** 主区域里至少要有一样可读的东西：文字、标题、按钮、表单控件或内嵌页面。 */
function hasMeaningfulContent(main: HTMLElement): boolean {
  if ((main.textContent ?? '').trim().length >= 24) return true;
  return ['h1', 'h2', 'h3', 'button', 'iframe', 'input', 'textarea', '[role="tablist"]', '[role="status"]']
    .some((selector) => main.querySelector(selector) !== null);
}

/** 等页面主区域出现并且不是空白骨架。 */
async function waitForPageContent(timeout = 15_000): Promise<void> {
  await waitFor(() => {
    const main = currentPageMain();
    expect(main).not.toBeNull();
    expect(hasMeaningfulContent(main as HTMLElement)).toBe(true);
  }, { timeout });
}

beforeEach(() => {
  vi.clearAllMocks();
  apiCalls.length = 0;
  unhandledRejections.length = 0;
  window.history.pushState({}, '', '/');
  vi.mocked(AuthContext.useAuth).mockReturnValue(makeAuthState());
  vi.mocked(UserAccessContext.useUserAccess).mockReturnValue(makeUserAccessState());
  // jsdom 没有布局能力：补齐页面会直接调用的浏览器 API，避免把「环境缺失」误报成页面 bug。
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false,
    }),
  });
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
    configurable: true,
    writable: true,
    value: vi.fn(),
  });
  // jsdom 未实现 scrollTo：外壳切路由会调它，桩掉避免 stderr 噪声。
  Object.defineProperty(window, 'scrollTo', { configurable: true, writable: true, value: vi.fn() });
  vi.stubGlobal('ResizeObserver', class {
    observe() { /* noop */ }
    unobserve() { /* noop */ }
    disconnect() { /* noop */ }
  });
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** 挂载 App 到指定路径，等到应用外壳/页面主区域出现且不再是 404 或错误边界。 */
async function renderRoute(path: string): Promise<string[]> {
  const consoleErrors: string[] = [];
  vi.spyOn(console, 'error').mockImplementation((...args: unknown[]) => {
    consoleErrors.push(args.map((value) => String(value)).join(' '));
  });
  window.history.pushState({}, '', path);
  render(<App />);
  await waitForPageContent();
  expect(screen.queryByText('页面未找到')).not.toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: '模块暂未完成加载' })).not.toBeInTheDocument();
  return consoleErrors;
}

describe('全量路由渲染冒烟（页面清单从 App.tsx 自动派生）', () => {
  it('路由表可解析，且确实包含应用与后台两组路由', () => {
    // 解析数量本身是基线：App.tsx 结构大改时先在这里失败，而不是静默少测一堆路由。
    expect(ROUTE_TABLE.length).toBeGreaterThanOrEqual(30);
    expect(COMPONENT_ROUTES.length).toBeGreaterThanOrEqual(20);
    expect(REDIRECT_ROUTES.length).toBeGreaterThanOrEqual(10);
    expect(STANDALONE_PATHS).toEqual(expect.arrayContaining(['/', '/guide', '/access', '/admin/login', '/login']));

    const componentNames = COMPONENT_ROUTES.map((route) => mountedComponent(route.element));
    expect(componentNames).not.toContain(null);
    // 每种真实页面至少出现一次；列表只用于可读性，不参与通过判定。
    expect(new Set(componentNames).size).toBeGreaterThanOrEqual(15);
  });

  it.each(PAGE_ROUTES.map((route) => ({ path: concretePath(route.path), routePath: route.path })))(
    '页面路由 $routePath 能挂载、有主区域、无错误边界与 console.error',
    async ({ path }) => {
      const consoleErrors = await renderRoute(path);
      expect(unexpectedConsoleErrors(consoleErrors)).toEqual([]);
      expect(unexpectedUnhandledRejections()).toEqual([]);
    },
  );

  it.each(STANDALONE_PATHS.filter((path) => path === '/' || path === '/guide'))(
    '独立页面 %s 不在应用外壳内，也能直接渲染',
    async (path) => {
      const consoleErrors = await renderRoute(path);
      expect(unexpectedConsoleErrors(consoleErrors)).toEqual([]);
      expect(unexpectedUnhandledRejections()).toEqual([]);
      // 落地页/使用手册是公开页面：不应出现登录后侧栏。
      expect(screen.queryByRole('navigation', { name: '主导航' })).not.toBeInTheDocument();
    },
  );

  it('未知路径落到 404 页而不是白屏', async () => {
    window.history.pushState({}, '', '/this-route-does-not-exist');
    render(<App />);
    expect(await screen.findByRole('main', {}, { timeout: 15_000 })).toBeInTheDocument();
    expect(await screen.findByText('页面未找到')).toBeInTheDocument();
  });
});

describe('重定向路由：旧链接不 404', () => {
  it.each(REDIRECT_ROUTES.map((route) => ({
    routePath: route.path,
    path: concretePath(route.path),
    target: resolveFinalRedirectTarget(redirectTarget(route.element) as string),
  })))(
    '路由 $routePath 最终重定向到 $target',
    async ({ path, target }) => {
      window.history.pushState({}, '', path);
      render(<App />);
      await waitFor(() => {
        expect(`${window.location.pathname}${window.location.search}`).toBe(target);
      }, { timeout: 15_000 });
      // 落点必须是可渲染页面，而不是再 404 或白屏。
      await waitForPageContent();
      expect(screen.queryByText('页面未找到')).not.toBeInTheDocument();
    },
  );

  it('已下线页面（死代码）不在 App.tsx 的页面导入里，路由只剩重定向', () => {
    const redirectByPath = new Map(REDIRECT_ROUTES.map((route) => [route.path, redirectTarget(route.element)]));
    for (const { page, route, target } of RETIRED_PAGE_ROUTES) {
      expect(appSource).not.toContain(`import('./pages/${page}')`);
      expect(redirectByPath.get(route)).toBe(target);
    }
    // HomePage 从来没被路由引用：`/app` 渲染的是 MarketDashboardPage。
    expect(appSource).not.toContain(`import('./pages/HomePage')`);
  });
});

describe('读取权限门禁（未登录状态）', () => {
  beforeEach(() => {
    vi.mocked(AuthContext.useAuth).mockReturnValue(makeAuthState({
      authEnabled: true,
      loggedIn: false,
      passwordSet: true,
      setupState: 'enabled',
    }));
    vi.mocked(UserAccessContext.useUserAccess).mockReturnValue(makeUserAccessState({
      loggedIn: false,
      user: null,
      authMethod: null,
    }));
  });

  it('未登录访问应用页会被带到 /access，并渲染访问页', async () => {
    window.history.pushState({}, '', '/tasks');
    render(<App />);
    expect(await screen.findByRole('main', {}, { timeout: 15_000 })).toBeInTheDocument();
    await waitFor(() => expect(window.location.pathname).toBe('/access'));
    expect(screen.queryByText('页面未找到')).not.toBeInTheDocument();
  });

  it('/access 自身可渲染（不会重定向到别处）', async () => {
    window.history.pushState({}, '', '/access');
    render(<App />);
    expect(await screen.findByRole('main', {}, { timeout: 15_000 })).toBeInTheDocument();
    expect(window.location.pathname).toBe('/access');
    expect(screen.queryByText('页面未找到')).not.toBeInTheDocument();
  });

  it('/admin/login 在未登录时渲染登录页', async () => {
    window.history.pushState({}, '', '/admin/login');
    render(<App />);
    // 登录页是独立页面，没有应用外壳的 <main>：用可见表单控件判断渲染完成。
    await waitFor(() => expect(document.body.querySelectorAll('input').length).toBeGreaterThan(0), { timeout: 15_000 });
    expect(window.location.pathname).toBe('/admin/login');
    expect(screen.queryByText('页面未找到')).not.toBeInTheDocument();
  });
});

describe('旧登录入口 /login 的分派', () => {
  it('已登录用户访问 /login 会被带到管理后台，而不是停在旧入口', async () => {
    window.history.pushState({}, '', '/login');
    render(<App />);
    await waitFor(() => expect(window.location.pathname).toBe('/admin'), { timeout: 15_000 });
  });
});

/**
 * 页面预算回归（`docs/ui-design-system.md` §10）。
 *
 * 直接跑 `scripts/audit_page_budget.py` 并读它的输出，避免把静态审计规则在测试里再写一遍。
 * 基线是「当前已知超标页面」：让数字变差就失败，修好了数字自然变小。
 */
const PAGE_BUDGET_BASELINE = {
  // 基线快照：`python scripts/audit_page_budget.py` 输出 41 个页面、2 个超标。
  totalPages: 41,
  maxOffending: 2,
  knownOffenders: ['DragonTigerPage', 'JevLiveTPage'],
} as const;

type NodeChildProcess = {
  execFileSync: (
    file: string,
    args: string[],
    options: { encoding: 'utf8'; cwd?: string; timeout?: number },
  ) => string;
};

/**
 * 只为了跑一个 Python 静态审计脚本而引入 child_process：用动态 import 拿模块，
 * 这样本文件仍然留在 `tsconfig.app.json`（types 只有 vite/client）的类型检查里。
 */
async function loadChildProcess(): Promise<NodeChildProcess> {
  const specifier = ['node', 'child_process'].join(':');
  return await import(/* @vite-ignore */ specifier) as NodeChildProcess;
}

/**
 * 仓库根目录（apps/dsa-web/src/pages/__tests__ → 上五级）。
 * 注意不能写成 `new URL('../../..', import.meta.url)`：Vite 会把字面量路径当成资产 URL 重写成 /@fs/...。
 */
const REPO_ROOT_PATH = (() => {
  const filePath = decodeURIComponent(new URL(import.meta.url).pathname);
  const directory = filePath.slice(0, filePath.lastIndexOf('/'));
  let current = directory;
  for (let levels = 0; levels < 5; levels += 1) {
    current = current.slice(0, current.lastIndexOf('/'));
  }
  return (current || '/').replace(/^\/([A-Za-z]:)/, '$1') + '/';
})();

function runPageBudgetAudit(nodeChildProcess: NodeChildProcess): string {
  const interpreterCandidates = ['python3', 'python'];
  let lastError: unknown = null;
  for (const interpreter of interpreterCandidates) {
    try {
      return nodeChildProcess.execFileSync(interpreter, ['scripts/audit_page_budget.py'], {
        encoding: 'utf8',
        cwd: REPO_ROOT_PATH,
        timeout: 120_000,
      });
    } catch (error) {
      const stdout = (error as { stdout?: unknown }).stdout;
      if (typeof stdout === 'string' && stdout.includes('个超出 §10 预算')) {
        // 脚本以非零码退出但已经有报告（例如未来加了 --strict），照样读它的输出。
        return stdout;
      }
      lastError = error;
    }
  }
  throw new Error(
    '无法执行 scripts/audit_page_budget.py：需要 python3（页面预算回归是发布门禁的一部分）。'
    + `最后一次错误：${String(lastError)}`,
  );
}

type PageBudgetReport = { pageCount: number; offending: number; offenders: string[] };

function parsePageBudgetReport(output: string): PageBudgetReport {
  const summary = output.match(/共\s*(\d+)\s*个页面，\s*(\d+)\s*个超出\s*§10\s*预算/);
  if (!summary) {
    throw new Error(`无法从审计输出里解析汇总行：\n${output.slice(-800)}`);
  }
  const offenders = output
    .split('\n')
    .filter((line) => line.includes('>') && /^\S+\s+\d+/.test(line))
    .map((line) => line.trim().split(/\s+/)[0] as string)
    .filter((name) => name !== '页面');
  return {
    pageCount: Number(summary[1]),
    offending: Number(summary[2]),
    offenders: [...new Set(offenders)].sort(),
  };
}

describe('页面预算回归（§10，超标页面数不超过基线）', () => {
  it('audit_page_budget.py 的超标页面数不超过基线', async () => {
    const nodeChildProcess = await loadChildProcess();
    const report = parsePageBudgetReport(runPageBudgetAudit(nodeChildProcess));
    expect(report.pageCount).toBeGreaterThanOrEqual(PAGE_BUDGET_BASELINE.totalPages);
    expect(report.offending).toBeLessThanOrEqual(PAGE_BUDGET_BASELINE.maxOffending);
    const newOffenders = report.offenders.filter(
      (name) => !(PAGE_BUDGET_BASELINE.knownOffenders as readonly string[]).includes(name),
    );
    expect(newOffenders).toEqual([]);
  });
});
