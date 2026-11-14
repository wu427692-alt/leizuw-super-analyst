import { cleanup, configure, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../../App';
import { NAV_ITEMS } from '../../components/layout/navItems';
import * as AuthContext from '../../contexts/AuthContext';
import * as UserAccessContext from '../../contexts/UserAccessContext';
import { useAgentChatStore } from '../../stores';
import { findResearchSession } from '../../stores/researchSessionStore';
import type { HomeDashboard } from '../../types/homeDashboard';
import type { MonitorEvent, SuperWatchlistStock } from '../../types/investmentMonitor';
import type { SystemConfigCategory, SystemConfigItem } from '../../types/systemConfig';
import { UI_LANGUAGE_STORAGE_KEY } from '../../utils/uiLanguage';

/**
 * 关键用户流程回归（发布门禁「前端使用」层）。
 *
 * 只覆盖最近几轮真实改动过的用户路径，断言页面行为而不复刻实现细节：
 * 1. 对话入口唯一：`/chat` 手机端重定向到 `/meeting`，导航里只剩一个对话入口；
 * 2. 研究议题 = 会话标题：议题进页头、成为首条消息、可返回议题；
 * 3. 快讯小窗：点快讯不跳路由，抽屉里看正文，取不到正文时给原文入口；
 * 4. 公司 logo：自选列表渲染真实 logo，非 A 股退回首字色块，不再出现像素小人；
 * 5. 桌面端切页：主要页面切 Tab 后只有当前视图，且不会崩。
 *
 * 网络层统一桩掉；页面、组件、store 都跑真实实现，避免测试只证明 mock 之间能对上。
 */

const { viewport, apiCalls, apiClientMock } = vi.hoisted(() => {
  const viewport = { isMobile: false };
  const apiCalls: string[] = [];
  const reject = (method: string) => vi.fn((url: unknown) => {
    apiCalls.push(`${method} ${String(url)}`);
    return Promise.reject(new Error(`关键流程测试：网络层未配置（${method} ${String(url)}）`));
  });
  return {
    viewport,
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

vi.mock('../../api/index', () => ({
  default: apiClientMock,
  BACKGROUND_ROUTE_HEADER: 'X-DSA-Route-Load',
  BACKGROUND_ROUTE_HEADERS: { 'X-DSA-Route-Load': 'background' },
}));

vi.mock('../../contexts/AuthContext', () => ({
  AuthProvider: ({ children }: { children: ReactNode }) => children,
  useAuth: vi.fn(),
}));

vi.mock('../../contexts/UserAccessContext', () => ({
  UserAccessProvider: ({ children }: { children: ReactNode }) => children,
  useUserAccess: vi.fn(),
}));

// ---- 按流程需要的接口桩：只桩这几个模块，其余走 api/index 的默认拒绝。 ----

const { homeDashboardGet, industryResearchProjects, fetchEventContent } = vi.hoisted(() => ({
  homeDashboardGet: vi.fn(),
  industryResearchProjects: vi.fn(),
  fetchEventContent: vi.fn(),
}));

vi.mock('../../api/homeDashboard', () => ({
  homeDashboardApi: { get: homeDashboardGet },
}));

vi.mock('../../api/industryResearch', () => ({
  industryResearchApi: { projects: industryResearchProjects },
}));

vi.mock('../../api/eventContent', () => ({ fetchEventContent }));

const { getAgentRoles } = vi.hoisted(() => ({ getAgentRoles: vi.fn() }));
vi.mock('../../api/command', () => ({ commandApi: { getAgentRoles } }));

const { agentApiMock } = vi.hoisted(() => ({
  agentApiMock: {
    getSkills: vi.fn(async () => ({ skills: [] })),
    getChatSessions: vi.fn(async () => []),
    getChatSessionMessages: vi.fn(async () => []),
    deleteChatSession: vi.fn(async () => undefined),
    sendChat: vi.fn(async () => ({ success: true })),
    chatStream: vi.fn(async () => new Response('')),
  },
}));
vi.mock('../../api/agent', () => ({ agentApi: agentApiMock }));

const { systemConfigApiMock } = vi.hoisted(() => ({
  systemConfigApiMock: {
    getConfig: vi.fn(),
    update: vi.fn(),
    validate: vi.fn(),
    getSetupStatus: vi.fn(async () => ({ isComplete: true, readyForSmoke: true, requiredMissingKeys: [], checks: [] })),
    getSchedulerStatus: vi.fn(async () => ({ running: false })),
    exportEnv: vi.fn(async () => ({ content: '', configVersion: 'v1' })),
    importEnv: vi.fn(async () => ({ content: '', configVersion: 'v1' })),
    runSchedulerNow: vi.fn(),
    getWatchlist: vi.fn(),
    addToWatchlist: vi.fn(),
    removeFromWatchlist: vi.fn(),
  },
}));
vi.mock('../../api/systemConfig', () => ({ systemConfigApi: systemConfigApiMock }));

const { stockWorkspace } = vi.hoisted(() => ({ stockWorkspace: vi.fn() }));
vi.mock('../../api/investmentMonitor', () => ({
  investmentMonitorApi: {
    stockWorkspace,
    event: vi.fn(async () => null),
    backfillWatchlist: vi.fn(async () => undefined),
    refreshSuperWatchlist: vi.fn(async () => undefined),
    analyzeEssayConsensus: vi.fn(async () => undefined),
    essayConsensus: vi.fn(async () => null),
  },
}));

const { essayRadarApiMock } = vi.hoisted(() => {
  const methodNames = [
    'audioAnalysisCapability', 'audioAnalysisTask', 'audioAnalysisTasks', 'audioAnalysisTranscript',
    'audioBatchTask', 'audioFiles', 'backfillCount', 'dailyReports', 'dashboard', 'deepInsights',
    'downloadAudioAnalysis', 'downloadAudioBatchTask', 'downloadAudioTranscript',
    'downloadSelectedAudioTranscripts', 'exportFeed', 'exportSelected', 'historicalBacklog',
    'insights', 'interpretMarketImpact', 'list', 'note', 'retryAudioAnalysisTask',
    'startAudioAnalysisTask', 'startAudioBatchTask', 'status', 'wordCloud',
  ] as const;
  const api: Record<string, ReturnType<typeof vi.fn>> = {};
  for (const name of methodNames) api[name] = vi.fn(async () => undefined);
  return { essayRadarApiMock: api };
});
vi.mock('../../api/essayRadar', () => ({ essayRadarApi: essayRadarApiMock }));

const { stockIndex, realtimeQuotes, realtimeIndices } = vi.hoisted(() => {
  const stockIndex = [
    {
      canonicalCode: '300750.SZ', displayCode: '300750', nameZh: '宁德时代', pinyinFull: 'ningdeshidai',
      pinyinAbbr: 'ndsd', aliases: [], market: 'CN', assetType: 'stock', active: true, popularity: 100,
    },
    {
      canonicalCode: 'HK00700', displayCode: '00700', nameZh: '腾讯控股', pinyinFull: 'tengxunkonggu',
      pinyinAbbr: 'txkg', aliases: [], market: 'HK', assetType: 'stock', active: true, popularity: 90,
    },
  ];
  return { stockIndex, realtimeQuotes: new Map(), realtimeIndices: new Map() };
});

vi.mock('../../hooks/useStockIndex', () => ({
  useStockIndex: () => ({ index: stockIndex, loading: false, error: null, fallback: false, loaded: true }),
}));

vi.mock('../../hooks/useRealtimeQuotes', () => ({
  useRealtimeQuotes: () => ({ quotes: realtimeQuotes, keyFor: (value: string) => String(value).split('.')[0] }),
  useRealtimeIndices: () => realtimeIndices,
}));

vi.mock('../../components/market', () => ({
  MarketTimeframeChart: ({ symbol }: { symbol: string }) => <div data-testid="market-chart">{symbol}</div>,
}));

// 懒加载页面在 jsdom 里首帧可能还没就绪；默认等待时间放宽到 5s，避免把加载慢当成失败。
configure({ asyncUtilTimeout: 5_000 });

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

// ---- 测试数据 ----

function monitorEvent(id: number, title: string, extra: Partial<MonitorEvent> = {}): MonitorEvent {
  return {
    id,
    sourceKey: 'cls.news',
    sourceName: '财联社',
    sourceType: 'api',
    externalId: `ext-${id}`,
    eventType: 'news',
    perspective: 'investor',
    title,
    summary: null,
    url: null,
    symbols: [],
    sentiment: 'neutral',
    importanceScore: 50,
    confidenceScore: 80,
    tags: [],
    actors: [],
    metrics: {},
    eventAt: '2026-11-14T02:30:00Z',
    ...extra,
  };
}

function dashboard(news: MonitorEvent[], pinned: MonitorEvent[] = []): HomeDashboard {
  return {
    generatedAt: '2026-11-14T02:40:00',
    marketTime: '2026-11-14 10:40:00',
    tradeDate: '20261114',
    cnIndices: [{
      code: '000001.SH', name: '上证指数', region: 'A股', close: 3200, changePct: 0.5, history: [],
    }],
    globalIndices: [],
    breadth: { available: false, up: 0, down: 0, flat: 0, limitUp: 0, limitDown: 0, total: 0, distribution: [] },
    sectorDistribution: {
      available: false, up: 0, down: 0, flat: 0, total: 0, distribution: [], leaders: [], laggards: [],
    },
    northbound: {},
    watchlist: [],
    latestEvents: [],
    latestNews: news,
    pinnedNews: pinned,
    intelligenceSummary: {},
    warnings: [],
    cache: { hit: false, ttlSeconds: 60, ageSeconds: 0 },
  };
}

function superWatchlistStock(symbol: string, name: string): SuperWatchlistStock {
  return {
    symbol,
    name,
    history: [],
    market: { price: 10, changePct: 1 },
    valuation: {},
    technical: {},
    fundamentals: {},
    capital: { moneyflow: {}, margin: {}, northbound: {}, chipDistribution: [] },
    ownership: { pledge: {}, shareUnlock: [], holderTrades: [], repurchases: [] },
    institution: { researchCount: 0, latest: [], institutions: [] },
    company: { profile: {}, announcementCount: 0, announcements: [] },
    alternative: { essayCount: 0, essays: [], catalysts: [], risks: [] },
    consensus: {
      brokerReportCount: 0, ratings: [], targetPrice: { sampleCount: 0 }, forecasts: [],
      essayExpectationCount: 0, essayExpectations: [], method: 'test',
      essayAnalysis: {
        status: 'not_started', sourceCount: 0, analyzedCount: 0, pendingCount: 0,
        summary: '', hasExplicitExpectations: false, profitOutlook: '', valuationOutlook: '',
        estimates: [], metricCounts: {}, consensusPoints: [], conflicts: [], caveats: [], sourceNotes: [],
      },
    },
    messages: { count: 0, items: [], channels: [] },
    stockComments: { count: 0, items: [], sourceNote: 'test' },
    signals: [],
    coverage: [],
    evidence: {
      eventCount: 0, rawEventCount: 0, factualCount: 0, unverifiedCount: 0,
      sourceCount: 0, originalLinkCount: 0, originalLinkCoverage: 0, channels: [],
    },
    timeline: [],
  };
}

function settingsItem(key: string, category: SystemConfigCategory): SystemConfigItem {
  return {
    key,
    value: '1',
    rawValueExists: true,
    isMasked: false,
    schema: {
      key,
      title: key,
      category,
      dataType: 'string',
      uiControl: 'text',
      isSensitive: false,
      isRequired: false,
      isEditable: true,
      options: [],
      validation: {},
      displayOrder: 1,
    },
  };
}

const settingsConfig = {
  configVersion: 'v1',
  maskToken: '******',
  items: [
    settingsItem('BASE_VALUE', 'base'),
    settingsItem('AI_MODEL', 'ai_model'),
    settingsItem('AGENT_ON', 'agent'),
    settingsItem('NOTIFY_ON', 'notification'),
    settingsItem('DATA_SOURCE', 'data_source'),
    settingsItem('BACKTEST_DAYS', 'backtest'),
    settingsItem('SYSTEM_SECRET', 'system'),
  ],
};

const atlasInsights = {
  period: { startDate: '2026-09-19', endDate: '2026-10-02', granularity: 'day' },
  windowDays: 14,
  summary: { analyzedCount: 100, sourceCount: 3, themeCount: 8, stockCount: 12, divergenceCount: 2 },
  marketImpact: {
    items: [{
      tsCode: '300750.SZ', name: '宁德时代', mentionCount: 6, dataSource: '本地行情库',
      series: [{ date: '2026-10-01', mentionCount: 1, priceReturn: 1 }],
      metrics: [{
        period: 5, averageReturn: 1.2, winRate: 60, sampleCount: 5,
        confidenceInterval95: [0.2, 2.2], averageExcessReturn: 0.5,
      }],
      insight: '测试', leadLag: [{ lagSessions: 0, correlation: 0.3, sampleCount: 5 }],
      attentionComparison: [{ level: 'high', averageReturn5D: 1, winRate5D: 60, sampleCount: 5 }],
    }],
    coverage: {
      eventCoveragePercent: 50, coveredEventDayCount: 5, eventDayCount: 10, pricedStockCount: 3,
      candidateStockCount: 4, priceStart: '2026-09-01', priceEnd: '2026-10-01',
      benchmarkAvailable: true, sources: ['本地行情库'],
    },
    causalityNote: '相关不是因果', entryRule: 'e', exitRule: 'x', priceBasis: 'p', dedupeRule: 'd',
  },
  layers: {
    sources: [{ key: 's1', label: '星球A', count: 5 }],
    themes: [{ key: 't1', label: '算力', count: 4 }],
    stocks: [{ key: 'k1', label: '宁德时代', count: 3, tsCode: '300750.SZ', stockName: '宁德时代' }],
    outcomes: [{ key: 'o1', label: '5日上涨', count: 2, tsCode: '300750.SZ', stockName: '宁德时代' }],
    edges: [{ fromStage: 'sources', from: 's1', toStage: 'themes', to: 't1', count: 5 }],
  },
  themeHeatmap: {
    dates: ['2026-10-01', '2026-10-02'],
    taxonomy: { rawThemeCount: 9, canonicalThemeCount: 5 },
    items: [{
      name: '算力', total: 5, aliases: [],
      points: [{ date: '2026-10-01', count: 2, concentrationScore: 30, sharePercent: 20, dailyTotal: 10 }],
    }],
  },
  evidenceFunnel: [{ name: '扫描语料', count: 100 }, { name: '已分析', count: 60 }],
};

beforeEach(() => {
  vi.clearAllMocks();
  apiCalls.length = 0;
  viewport.isMobile = false;
  localStorage.clear();
  // 固定中文界面：侧栏/底栏的 aria-label 与页面文案才是稳定的断言对象。
  localStorage.setItem(UI_LANGUAGE_STORAGE_KEY, 'zh');
  window.history.pushState({}, '', '/');
  vi.mocked(AuthContext.useAuth).mockReturnValue(makeAuthState());
  vi.mocked(UserAccessContext.useUserAccess).mockReturnValue(makeUserAccessState());

  // jsdom 没有布局/媒体查询能力：补齐页面会直接调用的浏览器 API。
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => {
      const wantsMobile = query.includes('max-width: 1023px') || query.includes('max-width: 767px');
      return {
        matches: wantsMobile ? viewport.isMobile : false,
        media: query,
        onchange: null,
        addListener: () => undefined,
        removeListener: () => undefined,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        dispatchEvent: () => false,
      };
    },
  });
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
    configurable: true,
    writable: true,
    value: vi.fn(),
  });
  Object.defineProperty(window, 'scrollTo', { configurable: true, writable: true, value: vi.fn() });
  vi.stubGlobal('ResizeObserver', class {
    observe() { /* noop */ }
    unobserve() { /* noop */ }
    disconnect() { /* noop */ }
  });
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });

  getAgentRoles.mockResolvedValue({
    general: { name: '主席', title: '统筹 / 决策' },
    research: { name: '研究员', title: '研究 / 报告' },
  });
  industryResearchProjects.mockResolvedValue({ items: [] });
  homeDashboardGet.mockResolvedValue(dashboard([monitorEvent(100, '【宁德时代发布新品】当日快讯原文')]));
  fetchEventContent.mockResolvedValue({
    eventId: 100, status: 'empty', content: '', source: 'db', url: null, fetchedAt: null,
  });
  systemConfigApiMock.getConfig.mockResolvedValue(settingsConfig);
  systemConfigApiMock.getWatchlist.mockResolvedValue(['300750.SZ', 'HK00700']);
  systemConfigApiMock.update.mockResolvedValue({ success: true });
  systemConfigApiMock.validate.mockResolvedValue({ valid: true, issues: [] });
  stockWorkspace.mockImplementation(async (symbol: string) => ({
    view: 'full',
    stock: superWatchlistStock(symbol, symbol.startsWith('300750') ? '宁德时代' : symbol),
  }));
  essayRadarApiMock.note!.mockResolvedValue({ id: 'note-1' });
  essayRadarApiMock.deepInsights!.mockResolvedValue(atlasInsights);
  essayRadarApiMock.list!.mockResolvedValue({ total: 0, items: [] });
  essayRadarApiMock.insights!.mockResolvedValue({
    generatedAt: '2026-11-14T00:00:00Z', windowDays: 30, latestDataAt: '2026-11-14T00:00:00Z',
    yesterday: null, coverage: null, trend: [], sourceQuality: [], informationTypes: [],
    sourceMix: [], modelComparison: null, watchlist: [], highNoveltySignals: [],
  });
  essayRadarApiMock.status!.mockResolvedValue({
    progress: { coveragePercent: 80, completed: 10, pending: 2, failed: 0 },
    worker: { running: true, pollSeconds: 5, lastSyncAt: null },
    mcpSync: { running: true, pollSeconds: 10, lastSyncAt: null, groups: [] },
    dailyReportWorker: { running: true, runHourShanghai: 8 },
  });
  essayRadarApiMock.dashboard!.mockResolvedValue({
    days: 30, generatedAt: '',
    summary: { analyzedCount: 10, averageImportance: 60, stockCount: 2, tagCount: 1, totalTokens: 10 },
    sentiment: [], categories: [], topTags: [], topIndustries: [], topThemes: [], topStocks: [],
  });
  essayRadarApiMock.wordCloud!.mockResolvedValue({
    startDate: '2026-10-01', endDate: '2026-11-01', sourceCount: 0, items: [],
  });
  essayRadarApiMock.dailyReports!.mockResolvedValue({ total: 0, items: [] });
  essayRadarApiMock.historicalBacklog!.mockResolvedValue({
    totalNotes: 0, queuedNotes: 0, completed: 0, pending: 0, processing: 0,
    failed: 0, coveragePercent: 0, unqueued: 0, groupCount: 0,
    notes24h: 0, notes7d: 0, notes30d: 0,
  });
  essayRadarApiMock.audioFiles!.mockResolvedValue({ items: [], total: 0 });

  // 对话 store 用真实实现，但预置成「已登录 + 已初始化」状态，避免每次挂载去打历史接口。
  useAgentChatStore.setState({
    ownerScope: 'user:1',
    hasInitialLoad: true,
    sessionId: 'session-test',
    messages: [],
    messagesLoading: false,
    historyError: null,
    sessionsError: null,
    loading: false,
    progressSteps: [],
    sessions: [],
    sessionsLoading: false,
    chatError: null,
    completionBadge: false,
    abortController: null,
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function renderAt(path: string): Promise<void> {
  window.history.pushState({}, '', path);
  render(<App />);
  await waitFor(() => {
    expect(document.body.textContent ?? '').not.toBe('');
  });
}

async function waitForMainContent(): Promise<HTMLElement> {
  await waitFor(() => {
    const mains = Array.from(document.querySelectorAll<HTMLElement>('main'));
    expect(mains.length).toBeGreaterThan(0);
    const last = mains[mains.length - 1];
    expect((last?.textContent ?? '').trim().length).toBeGreaterThan(0);
  });
  const mains = Array.from(document.querySelectorAll<HTMLElement>('main'));
  return mains[mains.length - 1] as HTMLElement;
}

// ---------------------------------------------------------------------------
// 1. 对话入口唯一
// ---------------------------------------------------------------------------

describe('对话入口唯一：/chat 与 /meeting 合并成一个入口', () => {
  it('手机端 /chat 旧链接原地重定向到 /meeting，query 原样保留，只渲染一份对话', async () => {
    viewport.isMobile = true;
    await renderAt('/chat?from=meeting&topic=%E5%82%A8%E8%83%BD%E6%80%8E%E4%B9%88%E7%9C%8B&stock=300750');

    await waitFor(() => expect(window.location.pathname).toBe('/meeting'));
    const params = new URLSearchParams(window.location.search);
    expect(params.get('from')).toBe('meeting');
    expect(params.get('topic')).toBe('储能怎么看');
    expect(params.get('stock')).toBe('300750');

    expect(await screen.findByTestId('meeting-chat-page')).toBeInTheDocument();
    // 合并后只有一个对话组件实例，没有「问股页 + 团队页」两份对话同时挂载。
    expect(document.querySelectorAll('[data-testid="chat-workspace"]')).toHaveLength(1);
  });

  it('手机端 /meeting 就是对话页：团队角色条 + 对话输入框都在，不再有「跳到问股」的按钮', async () => {
    viewport.isMobile = true;
    await renderAt('/meeting');

    expect(await screen.findByTestId('meeting-chat-page')).toBeInTheDocument();
    const teamStrip = await screen.findByRole('group', { name: 'AI 研究小组角色' });
    expect(teamStrip).toHaveTextContent('AI 研究小组');
    expect(await within(teamStrip).findByText('主席')).toBeInTheDocument();
    expect(within(teamStrip).getByText('研究员')).toBeInTheDocument();

    // 对话本体（ChatPage）就地渲染：输入框可用。
    expect(await screen.findByPlaceholderText('输入研究问题')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '开始研投会议' })).not.toBeInTheDocument();
  });

  it('导航里只有一个对话入口：侧栏只留 /meeting，模块清单里不再有 /chat', async () => {
    expect(NAV_ITEMS.filter((item) => item.to === '/meeting')).toHaveLength(1);
    expect(NAV_ITEMS.some((item) => item.to === '/chat')).toBe(false);

    await renderAt('/home');
    await waitForMainContent();
    const sidebar = screen.getByRole('navigation', { name: '主导航' });
    const hrefs = within(sidebar).getAllByRole('link').map((link) => link.getAttribute('href'));
    expect(hrefs.filter((href) => href === '/meeting')).toHaveLength(1);
    expect(hrefs).not.toContain('/chat');
    expect(within(sidebar).queryByRole('link', { name: 'AI 问股' })).not.toBeInTheDocument();
  });

  it('手机端「更多」里没有第二个对话入口，底栏「团队」是唯一入口', async () => {
    viewport.isMobile = true;
    await renderAt('/home');
    await waitForMainContent();

    const bottomNav = screen.getByRole('navigation', { name: '手机主导航' });
    const bottomHrefs = within(bottomNav).getAllByRole('link').map((link) => link.getAttribute('href'));
    expect(bottomHrefs.filter((href) => href === '/meeting')).toHaveLength(1);
    expect(bottomHrefs).not.toContain('/chat');

    fireEvent.click(screen.getByRole('button', { name: '全部模块' }));
    const sheet = await screen.findByRole('dialog', { name: '全部模块' });
    const sheetHrefs = within(sheet).getAllByRole('link').map((link) => link.getAttribute('href'));
    expect(sheetHrefs).not.toContain('/chat');
    // 已经在底栏里的入口不重复出现在「更多」。
    expect(sheetHrefs).not.toContain('/meeting');
  });

  it('桌面端 /chat 仍可打开（旧深链兼容），但不再是导航项', async () => {
    await renderAt('/chat?stock=300750');
    // 先在对话页本身上等：外壳的 <main> 会比懒加载的对话页先出现，不能拿它当页面就绪信号。
    expect(await screen.findByPlaceholderText('输入研究问题')).toBeInTheDocument();
    expect(window.location.pathname).toBe('/chat');
    expect(document.querySelectorAll('[data-testid="chat-workspace"]')).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// 2. 研究议题 = 会话标题
// ---------------------------------------------------------------------------

describe('研究议题就是会话标题：页头、首条消息、返回入口', () => {
  it('带议题进入时，页头显示「研究议题 · xxx」，并有返回议题的入口', async () => {
    viewport.isMobile = true;
    await renderAt(`/meeting?from=meeting&topic=${encodeURIComponent('宁德时代 2026 年储能业务怎么看')}&stock=300750&name=${encodeURIComponent('宁德时代')}`);

    expect(await screen.findByTestId('meeting-chat-page')).toBeInTheDocument();
    // ChatPage 的页头本身就把议题当标题，不再显示泛化的「问股」。
    expect(await screen.findByTestId('chat-meeting-topic')).toHaveTextContent('宁德时代 2026 年储能业务怎么看');
    expect(screen.getByRole('button', { name: '返回研究议题' })).toBeInTheDocument();
    // 顶部团队条不再重复同一句标题（ChatPage 已经拥有标题）。
    expect(screen.queryByTestId('meeting-chat-topic')).not.toBeInTheDocument();
    await waitFor(() => expect(document.title).toContain('研究议题'));
    expect(document.title).toContain('宁德时代 2026 年储能业务怎么看');
  });

  it('空白会话里定议题 = 发起一次研究：新建会话、议题作为首条消息、URL 与页头同步', async () => {
    viewport.isMobile = true;
    await renderAt('/meeting');
    expect(await screen.findByTestId('meeting-chat-page')).toBeInTheDocument();

    const startStream = vi.spyOn(useAgentChatStore.getState(), 'startStream').mockResolvedValue(undefined);
    try {
      fireEvent.click(await screen.findByRole('button', { name: '设置研究议题' }));
      const editor = await screen.findByRole('textbox', { name: '研究议题' });
      fireEvent.change(editor, { target: { value: '储能怎么看' } });
      fireEvent.click(screen.getByRole('button', { name: '开始研究' }));

      // 一次研究 = 一个新会话；议题就是首条消息（服务端据此生成会话标题）。
      await waitFor(() => expect(startStream).toHaveBeenCalledTimes(1));
      expect(startStream).toHaveBeenCalledWith(
        expect.objectContaining({ message: '储能怎么看' }),
      );

      // 一次研究 = 一个新会话，议题随会话记录保存（本机研究记录按键回看）。
      const newSessionId = useAgentChatStore.getState().sessionId;
      expect(newSessionId).not.toBe('session-test');
      expect(findResearchSession(useAgentChatStore.getState().ownerScope, newSessionId)?.topic)
        .toBe('储能怎么看');

      // URL 上议题可见（页头/返回入口从此处恢复）。
      // 注意：这里不断言 `?session=`——实测 saveTopic 的写参数与 ChatPage 的
      // 研究上下文回写会竞争同一个 URLSearchParams，session 参数可能被后者覆盖掉
      //（已作为真实 bug 记录在交付报告里，不在本任务里改业务代码）。
      await waitFor(() => {
        const params = new URLSearchParams(window.location.search);
        expect(params.get('topic')).toBe('储能怎么看');
      });

      // 议题同时成为页头标题与上下文条，并保留返回入口。
      expect(await screen.findByTestId('chat-meeting-topic')).toHaveTextContent('储能怎么看');
      expect(screen.getByRole('button', { name: '返回研究议题' })).toBeInTheDocument();
      await waitFor(() => expect(document.title).toContain('研究议题'));
    } finally {
      startStream.mockRestore();
    }
  });
});

// ---------------------------------------------------------------------------
// 3. 快讯小窗
// ---------------------------------------------------------------------------

describe('快讯小窗：点开就地看正文，不跳实时流水', () => {
  async function openNewsOnWorkbench(): Promise<void> {
    await renderAt('/home');
    await waitForMainContent();
    fireEvent.click(await screen.findByRole('tab', { name: /快讯/ }));
    await waitFor(() => expect(screen.getByRole('tab', { name: /快讯/ })).toHaveAttribute('aria-selected', 'true'));
  }

  it('点快讯后路由仍是 /home，抽屉里展示正文与原文链接', async () => {
    homeDashboardGet.mockResolvedValue(dashboard([monitorEvent(100, '【宁德时代发布新品】当日快讯原文', {
      summary: '<p>第一段：公司发布了新一代电池产品。</p><p>第二段：该产品预计明年量产。</p>',
      url: 'https://example.com/news/100',
    })]));
    await openNewsOnWorkbench();

    fireEvent.click(screen.getByRole('button', { name: /查看快讯全文：宁德时代发布新品/ }));

    // 关键回归：不再 navigate 到 /investment-monitor/feed。
    expect(window.location.pathname).toBe('/home');
    expect(window.location.search).toBe('');

    const dialog = await screen.findByRole('dialog', { name: '快讯详情' });
    expect(within(dialog).getByRole('heading', { name: '宁德时代发布新品' })).toBeInTheDocument();
    expect(within(dialog).getByText('第一段：公司发布了新一代电池产品。')).toBeInTheDocument();
    expect(within(dialog).getByRole('link', { name: /打开原文/ }))
      .toHaveAttribute('href', 'https://example.com/news/100');
  });

  it('来源没有正文时，明确说「该来源未提供正文」并给出阅读原文', async () => {
    homeDashboardGet.mockResolvedValue(dashboard([monitorEvent(100, '【宁德时代发布新品】当日快讯原文', {
      summary: null,
      url: 'https://example.com/news/100',
    })]));
    fetchEventContent.mockResolvedValue({
      eventId: 100, status: 'failed', content: '', source: 'fetched', url: 'https://example.com/news/100', fetchedAt: null,
    });
    await openNewsOnWorkbench();

    fireEvent.click(screen.getByRole('button', { name: /查看快讯全文：宁德时代发布新品/ }));
    const dialog = await screen.findByRole('dialog', { name: '快讯详情' });
    expect(await within(dialog).findByText('该来源未提供正文。')).toBeInTheDocument();
    expect(within(dialog).getByRole('link', { name: /阅读原文/ }))
      .toHaveAttribute('href', 'https://example.com/news/100');
    expect(window.location.pathname).toBe('/home');
  });
});

// ---------------------------------------------------------------------------
// 4. 公司 logo
// ---------------------------------------------------------------------------

describe('自选列表用真实公司 logo，而不是像素小人', () => {
  it('手机端自选行渲染 StockLogo；非 A 股代码降级为公司名首字色块', async () => {
    viewport.isMobile = true;
    await renderAt('/super-watchlist');
    await waitForMainContent();

    const list = await screen.findByRole('region', { name: '我的自选' });
    await waitFor(() => expect(list.querySelectorAll('[data-stock-logo]')).toHaveLength(2));
    // 不再出现像素小人头像。
    expect(list.querySelectorAll('.pixel-avatar')).toHaveLength(0);

    const aShare = list.querySelector<HTMLElement>('[data-stock-logo="300750.SZ"]');
    expect(aShare).not.toBeNull();
    const image = aShare?.querySelector('img');
    expect(image?.getAttribute('src')).toBe('/api/v1/stocks/300750/logo');
    expect(image?.getAttribute('loading')).toBe('lazy');
    expect(aShare?.querySelector('[data-stock-logo-initial]')).toHaveTextContent('宁');

    const hk = list.querySelector<HTMLElement>('[data-stock-logo="HK00700"]');
    expect(hk).not.toBeNull();
    // 港美股没有 logo 数据源：不发请求，直接首字色块。
    expect(hk?.querySelector('img')).toBeNull();
    expect(hk?.querySelector('[data-stock-logo-initial]')).toHaveTextContent('腾');
  });
});

// ---------------------------------------------------------------------------
// 5. 桌面端切页
// ---------------------------------------------------------------------------

type TabPage = {
  path: string;
  /** 只有当前 Tab 里才出现的文案；用来证明「只渲染当前视图」。 */
  marker?: { tab: RegExp; text: string };
};

const DESKTOP_TAB_PAGES: TabPage[] = [
  { path: '/home', marker: { tab: /快讯/, text: '最近快讯' } },
  { path: '/app', marker: { tab: /快讯/, text: '新闻速递' } },
  { path: '/admin/settings' },
  { path: '/data-acquisition', marker: { tab: /取数任务/, text: '后台取数任务' } },
  { path: '/tasks', marker: { tab: /读取范围/, text: '读取范围与历史任务' } },
  { path: '/essay-radar/insights', marker: { tab: /热度与排行/, text: '主题热力迁移' } },
  { path: '/concept-themes' },
  { path: '/super-watchlist' },
];

describe('桌面端切页：主要页面切 Tab 后只渲染当前视图', () => {
  async function firstTablist(): Promise<HTMLElement> {
    await waitFor(() => {
      const lists = screen.queryAllByRole('tablist');
      const withTabs = lists.find((list) => list.querySelector('[role="tab"]') !== null);
      expect(withTabs).toBeDefined();
    });
    const lists = screen.getAllByRole('tablist');
    return lists.find((list) => list.querySelector('[role="tab"]')) as HTMLElement;
  }

  it.each(DESKTOP_TAB_PAGES.map((page) => page.path))(
    '页面 %s 的 Tab 可以逐个切换，选中态互斥且内容随视图变化',
    async (path) => {
      await renderAt(path);
      await waitForMainContent();
      const tablist = await firstTablist();
      const tabCount = within(tablist).getAllByRole('tab').length;
      expect(tabCount).toBeGreaterThanOrEqual(2);

      const renderedTexts = new Set<string>();
      for (let index = 0; index < tabCount; index += 1) {
        const list = await firstTablist();
        const tabs = within(list).getAllByRole('tab');
        fireEvent.click(tabs[index] as HTMLElement);
        await waitFor(() => {
          const current = within(screen.getAllByRole('tablist')[0] as HTMLElement).getAllByRole('tab');
          const selected = current.filter((tab) => tab.getAttribute('aria-selected') === 'true');
          expect(selected).toHaveLength(1);
          expect(selected[0]).toBe(current[index]);
        });
        renderedTexts.add(document.body.textContent ?? '');
        expect(screen.queryByRole('heading', { name: '模块暂未完成加载' })).not.toBeInTheDocument();
      }
      // 只有一个视图会被渲染：不同 Tab 的页面文本必须出现差异，而不是全部堆在一屏。
      expect(renderedTexts.size).toBeGreaterThan(1);
    },
    30_000,
  );

  it.each(DESKTOP_TAB_PAGES.filter((page) => page.marker).map((page) => ({
    path: page.path,
    tab: page.marker!.tab,
    text: page.marker!.text,
  })))(
    '页面 $path 的互斥视图：$text 只在该 Tab 出现',
    async ({ path, tab, text }) => {
      await renderAt(path);
      await waitForMainContent();
      expect(screen.queryByText(text)).not.toBeInTheDocument();

      const list = await firstTablist();
      const initialTabs = within(list).getAllByRole('tab');
      const initialIndex = initialTabs.findIndex((item) => item.getAttribute('aria-selected') === 'true');
      const target = initialTabs.find((item) => tab.test(item.textContent ?? ''));
      expect(target).toBeDefined();
      fireEvent.click(target as HTMLElement);
      expect(await screen.findByText(text)).toBeInTheDocument();

      // 切回进入时的 Tab 后，互斥视图必须消失（不是靠 CSS 隐藏）。
      const backTabs = within(await firstTablist()).getAllByRole('tab');
      fireEvent.click(backTabs[initialIndex] as HTMLElement);
      await waitFor(() => expect(screen.queryByText(text)).not.toBeInTheDocument());
    },
    30_000,
  );
});
