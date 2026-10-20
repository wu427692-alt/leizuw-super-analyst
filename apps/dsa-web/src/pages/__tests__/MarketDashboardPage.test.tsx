import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { StrictMode } from 'react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import apiClient from '../../api';
import { clearRequestCache } from '../../api/requestCache';
import type { RealtimeQuote } from '../../api/realtimeQuotes';
import { useUserAccess } from '../../contexts/UserAccessContext';
import { useRealtimeQuotes } from '../../hooks/useRealtimeQuotes';
import type { HomeDashboard, HomeWatchlistCard } from '../../types/homeDashboard';
import type { MonitorEvent } from '../../types/investmentMonitor';
import MarketDashboardPage from '../MarketDashboardPage';
import { investmentMonitorApi } from '../../api/investmentMonitor';

// Keep the real dashboard API and request cache: an unscoped cached response
// must not be accepted as the next account's supposedly fresh response.
vi.mock('../../api', () => ({ default: { get: vi.fn() }, BACKGROUND_ROUTE_HEADERS: {} }));
vi.mock('../../contexts/UserAccessContext', () => ({ useUserAccess: vi.fn() }));
vi.mock('../../api/investmentMonitor', () => ({ investmentMonitorApi: { event: vi.fn() } }));
vi.mock('../../hooks/useRealtimeQuotes', () => ({
  useRealtimeQuotes: vi.fn(),
  useRealtimeIndices: () => new Map(),
}));
vi.mock('../../api/marketSeries', () => ({ getMarketSeries: vi.fn().mockResolvedValue({}) }));
vi.mock('../../components/market', () => ({
  MarketTimeframeChart: ({ symbol }: { symbol: string }) => <div data-testid={`chart-${symbol}`} />,
}));

function event(id: number, title: string): MonitorEvent {
  return {
    id, title, sourceKey: 'test', sourceName: '测试来源', sourceType: 'test', externalId: String(id),
    eventType: 'news', perspective: 'institution', symbols: [], sentiment: 'neutral',
    importanceScore: 50, confidenceScore: 90, tags: [], actors: [], metrics: {}, eventAt: '2026-09-11T08:00:00Z',
  };
}
function card(symbol: string, name: string): HomeWatchlistCard {
  return {
    symbol, name, eventCount: 3, highPriorityCount: 1, opportunityScore: 20, riskScore: 10,
    perspectives: {}, sentiment: {}, institutionRatingCount: 0, history: [], latestEvents: [],
    latestQuote: { currentPrice: 10, changePercent: 1, updateTime: '2026-09-11T15:00:00', source: 'test' },
    latestInstitution: event(11, `${name}机构证据`), latestCatalyst: event(12, `${name}催化证据`),
    latestRisk: event(13, `${name}风险证据`),
  };
}
function dashboard(name = '账号 A 股票', symbol = '603306.SH'): HomeDashboard {
  return {
    generatedAt: '2026-09-11T15:00:00', marketTime: '2026-09-11 15:00:00', tradeDate: '20260911',
    cnIndices: [{ code: '000001.SH', name: '公共指数快照', region: 'A股', close: 3000, history: [] }],
    globalIndices: [],
    breadth: { available: false, up: 0, down: 0, flat: 0, limitUp: 0, limitDown: 0, total: 0, distribution: [] },
    sectorDistribution: { available: false, up: 0, down: 0, flat: 0, total: 0, distribution: [], leaders: [], laggards: [] },
    northbound: {}, watchlist: [card(symbol, name)], latestEvents: [event(99, `${name}关联情报`)],
    latestNews: [event(100, '公共财经新闻')], pinnedNews: [], intelligenceSummary: { eventCount: 37 },
    warnings: [`${name}私有告警`], cache: { hit: false, ttlSeconds: 60, ageSeconds: 0 },
  };
}
const auth = (id: number) => ({
  user: { id, name: `用户 ${id}`, status: 'approved' }, loggedIn: true, isLoading: false,
  authMethod: 'password', accessEnabled: true,
  registrationMode: 'approval' as const, smsLoginEnabled: false, sendSms: vi.fn(), smsLogin: vi.fn(), bindPhone: vi.fn(),
  register: vi.fn(), login: vi.fn(), logout: vi.fn(), refresh: vi.fn(),
});
function deferred() {
  let resolve!: (value: { data: HomeDashboard }) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<{ data: HomeDashboard }>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function LocationProbe() {
  const location = useLocation();
  return <output aria-label="当前路由">{location.pathname}{location.search}</output>;
}
function fixture() { return <MemoryRouter initialEntries={['/app']}><MarketDashboardPage /><LocationProbe /></MemoryRouter>; }
async function flush() { await act(async () => { await Promise.resolve(); }); }
// Match the hook's lossy lookup key. A matching Map slot does not prove that
// the payload belongs to this stock, especially across explicit exchanges.
const quoteKey = (symbol: string) => symbol.trim().toUpperCase().split('.')[0].replace(/^(SH|SZ|BJ)/, '');
function mockLiveQuotes(entries: Array<[string, RealtimeQuote]> = []) {
  vi.mocked(useRealtimeQuotes).mockReturnValue({ quotes: new Map(entries), error: null, keyFor: quoteKey });
}
function liveQuote(stockCode: string, overrides: Partial<RealtimeQuote> = {}): RealtimeQuote {
  return {
    stockCode, stockName: '来自报价的名称', currentPrice: 8, change: -2, changePercent: -20,
    open: 11, high: 12, low: 7, updateTime: '2026-09-11T15:01:00',
    source: 'tencent.snapshot', ...overrides,
  };
}
function expectFocusedQuote(price: string, percent: string, time: string, source: string, name = '账号 A 股票') {
  const detail = screen.getByRole('heading', { name }).closest('aside')!;
  expect(within(detail).getByText(price)).toBeInTheDocument();
  expect(within(detail).getByText(percent)).toBeInTheDocument();
  expect(within(detail).getByText(text => text.startsWith(`${time} · ${source}`))).toBeInTheDocument();
  expect(screen.getByRole('button', { name: new RegExp(`^${name}`) })).toHaveTextContent(`${price} ${percent}`);
}
function expectNoAccountA() {
  expect(screen.queryByText('账号 A 股票')).not.toBeInTheDocument();
  expect(screen.queryByText(/账号 A 股票.*证据/)).not.toBeInTheDocument();
  expect(screen.queryByTestId('chart-603306.SH')).not.toBeInTheDocument();
}

let testNumber = 0;
let accountA = 0;
let accountB = 0;
const cacheKey = (id: number) => `dsa:home-dashboard:last-good:v3:user:${id}:password:true`;

describe('MarketDashboardPage 个人自选股缓存隔离与证据入口', () => {
  beforeEach(() => {
    vi.useFakeTimers(); vi.resetAllMocks(); clearRequestCache(); localStorage.clear();
    testNumber += 1;
    // Expire module-level public snapshots between tests without a test-only
    // export or module reset that would duplicate React's runtime.
    vi.setSystemTime(new Date('2026-09-11T08:00:00Z').getTime() + testNumber * 24 * 60 * 60 * 1000);
    accountA = testNumber * 10 + 1; accountB = accountA + 1;
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    mockLiveQuotes();
    vi.mocked(useUserAccess).mockReturnValue(auth(accountA));
    vi.mocked(apiClient.get).mockResolvedValue({ data: dashboard() });
  });
  afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

  it.each([undefined, 'https://example.com/news'])('新闻无论有无外链均页内阅读，返回保留首页与股票（%s）', async url => {
    const value = dashboard();
    const news = { ...event(100, '新闻阅读测试'), url, sourceKey: 'tushare.news.cls', sourceName: 'Tushare 财联社快讯', summary: '新闻已保存摘录' };
    value.latestNews = [news];
    vi.mocked(apiClient.get).mockResolvedValue({ data: value });
    vi.mocked(investmentMonitorApi.event).mockResolvedValue(news);
    const external = vi.spyOn(window, 'open');
    render(fixture()); await flush();
    const trigger = screen.getByRole('button', { name: '查看新闻详情：新闻阅读测试' });
    expect(trigger).toHaveTextContent('财联社快讯');
    expect(trigger).not.toHaveTextContent(/tushare/i);
    trigger.focus(); fireEvent.click(trigger); await flush();
    expect(screen.getByLabelText('当前路由')).toHaveTextContent('/app');
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveTextContent('新闻已保存摘录');
    expect(dialog).not.toHaveTextContent(/tushare/i);
    expect(external).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole('button', { name: '返回首页' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByLabelText('当前路由')).toHaveTextContent('/app');
    expect(screen.getByTestId('chart-603306.SH')).toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('新闻详情失败仍可返回，账号退出立即卸载详情', async () => {
    vi.mocked(investmentMonitorApi.event).mockRejectedValue(new Error('timeout'));
    const view = render(fixture()); await flush();
    fireEvent.click(screen.getByRole('button', { name: '查看新闻详情：公共财经新闻' })); await flush();
    expect(screen.getByRole('dialog')).toHaveTextContent('本次详情读取未完成');
    fireEvent.click(screen.getByRole('button', { name: '返回首页' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查看新闻详情：公共财经新闻' })); await flush();
    vi.mocked(useUserAccess).mockReturnValue({ ...auth(accountA), loggedIn: false, user: null });
    view.rerender(fixture());
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('A 切换 B 立即隐藏 A，B 等待或失败仍只复用公共行情，随后准确恢复 B', async () => {
    const pendingB = deferred();
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: dashboard() }).mockReturnValueOnce(pendingB.promise)
      .mockResolvedValue({ data: dashboard('账号 B 股票', '300476.SZ') });
    const view = render(fixture()); await flush();
    expect(screen.getByRole('heading', { name: '账号 A 股票' })).toBeInTheDocument();
    vi.mocked(useUserAccess).mockReturnValue(auth(accountB));
    view.rerender(fixture());
    expectNoAccountA();
    expect(screen.getByText('正在读取当前账号的自选股…')).toBeInTheDocument();
    expect(screen.queryByText('暂无自选股数据')).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '公共指数快照' })).toBeInTheDocument();
    expect(screen.getByText('公共财经新闻')).toBeInTheDocument();
    await flush();
    expect(apiClient.get).toHaveBeenCalledTimes(2);
    await act(async () => { pendingB.reject(new Error('B timeout')); });
    expectNoAccountA();
    expect(screen.getByText(/市场数据暂时无法刷新/)).toBeInTheDocument();
    expect(screen.getByText('当前账号的自选股尚未读取成功，正在自动重试。')).toBeInTheDocument();
    expect(screen.queryByText('暂无自选股数据')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '刷新数据' })); await flush();
    expect(screen.getByRole('heading', { name: '账号 B 股票' })).toBeInTheDocument();
    expectNoAccountA();
    expect(localStorage.getItem(cacheKey(accountB))).toContain('账号 B 股票');
    expect(localStorage.getItem(cacheKey(accountB))).not.toContain('账号 A 股票');
  });

  it('旧账号在途请求不能覆盖 B 或写入缓存，返回 A 仍是 A 最后确认的快照', async () => {
    const pendingA = deferred(); const pendingB = deferred();
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: dashboard() }).mockReturnValueOnce(pendingA.promise)
      .mockResolvedValueOnce({ data: dashboard('账号 B 股票', '300476.SZ') }).mockReturnValueOnce(pendingB.promise);
    const view = render(fixture()); await flush();
    fireEvent.click(screen.getByRole('button', { name: '刷新数据' })); await flush();
    vi.mocked(useUserAccess).mockReturnValue(auth(accountB));
    view.rerender(fixture()); await flush();
    await act(async () => { pendingA.resolve({ data: dashboard('A 的迟到股票', '600000.SH') }); });
    expect(screen.getByRole('heading', { name: '账号 B 股票' })).toBeInTheDocument();
    expect(screen.queryByText('A 的迟到股票')).not.toBeInTheDocument();
    expect(localStorage.getItem(cacheKey(accountA))).not.toContain('A 的迟到股票');
    expect(localStorage.getItem('dsa:home-dashboard:last-good:v3:public')).not.toContain('A 的迟到股票');
    vi.mocked(useUserAccess).mockReturnValue(auth(accountA));
    view.rerender(fixture());
    expect(screen.getByRole('heading', { name: '账号 A 股票' })).toBeInTheDocument();
    expect(screen.queryByText('账号 B 股票')).not.toBeInTheDocument();
    await act(async () => { pendingB.reject(new Error('A refresh unavailable')); });
  });

  it('冷启动不读取旧 v2 混合缓存或其他账号持久缓存', async () => {
    localStorage.setItem('dsa:home-dashboard:last-good:v2', JSON.stringify({ storedAt: Date.now(), data: dashboard() }));
    localStorage.setItem(cacheKey(accountA), JSON.stringify({ storedAt: Date.now(), data: dashboard() }));
    vi.mocked(useUserAccess).mockReturnValue(auth(accountB));
    vi.mocked(apiClient.get).mockRejectedValue(new Error('unavailable'));
    render(fixture()); expectNoAccountA(); await flush(); expectNoAccountA();
  });

  it('StrictMode 重放仍接受同一身份的初始响应，不留下无限等待状态', async () => {
    const pending = deferred();
    vi.mocked(apiClient.get).mockReturnValue(pending.promise);
    render(<StrictMode>{fixture()}</StrictMode>);
    await act(async () => { pending.resolve({ data: dashboard() }); });
    expect(screen.getByRole('heading', { name: '账号 A 股票' })).toBeInTheDocument();
    expect(screen.queryByText('正在读取当前账号的自选股…')).not.toBeInTheDocument();
    expect(apiClient.get).toHaveBeenCalledTimes(1);
  });

  it('同一账号可读取自己的持久快照，但共享缓存不存自选、关联证据或私有汇总', async () => {
    localStorage.setItem(cacheKey(accountA), JSON.stringify({ storedAt: Date.now(), data: dashboard() }));
    render(fixture());
    expect(screen.getByRole('heading', { name: '账号 A 股票' })).toBeInTheDocument();
    await flush();
    const cached = JSON.parse(localStorage.getItem('dsa:home-dashboard:last-good:v3:public')!);
    expect(cached.data.watchlist).toEqual([]);
    expect(cached.data.latestEvents).toEqual([]);
    expect(cached.data.intelligenceSummary).toEqual({});
    expect(cached.data.warnings).toEqual([]);
    expect(JSON.stringify(cached)).not.toContain('账号 A 股票');
    expect(cached.data.cnIndices[0].name).toBe('公共指数快照');
  });

  it.each(['logout', 'revoked', 'loading'] as const)('%s 时立即卸载私有视图并停止首页请求', async mode => {
    const view = render(fixture()); await flush();
    vi.mocked(useUserAccess).mockReturnValue(mode === 'logout'
      ? { ...auth(accountA), loggedIn: false, user: null }
      : mode === 'loading' ? { ...auth(accountA), isLoading: true }
        : { ...auth(accountA), user: { id: accountA, name: '用户 A', status: 'pending' } });
    view.rerender(fixture()); expectNoAccountA();
    const calls = vi.mocked(apiClient.get).mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(65_000); });
    expect(apiClient.get).toHaveBeenCalledTimes(calls);
  });

  it('存储不可用仍可切换账号，不依赖浏览器持久化维持隔离', async () => {
    vi.spyOn(localStorage, 'getItem').mockImplementation(() => { throw new Error('storage disabled'); });
    vi.spyOn(localStorage, 'setItem').mockImplementation(() => { throw new Error('storage disabled'); });
    const view = render(fixture()); await flush();
    vi.mocked(useUserAccess).mockReturnValue(auth(accountB));
    vi.mocked(apiClient.get).mockResolvedValue({ data: dashboard('账号 B 股票', '300476.SZ') });
    view.rerender(fixture()); expectNoAccountA(); await flush();
    expect(screen.getByRole('heading', { name: '账号 B 股票' })).toBeInTheDocument();
  });

  it('证据原文跟随当前选中股票，机构/催化/风险直达精确事件，新闻渠道保持不变', async () => {
    const value = dashboard(); value.watchlist.push(card('300476.SZ', '第二只股票'));
    vi.mocked(apiClient.get).mockResolvedValue({ data: value });
    render(fixture()); await flush();
    fireEvent.click(screen.getByRole('button', { name: '证据原文' }));
    expect(screen.getByLabelText('当前路由')).toHaveTextContent('/investment-monitor/feed?symbol=603306.SH');
    fireEvent.click(screen.getByRole('button', { name: /第二只股票/ }));
    fireEvent.click(screen.getByRole('button', { name: '证据原文' }));
    expect(screen.getByLabelText('当前路由')).toHaveTextContent('/investment-monitor/feed?symbol=300476.SZ');
    for (const [label, id] of [['机构', 11], ['催化', 12], ['风险', 13]] as const) {
      expect(screen.getByRole('link', { name: `第二只股票${label}证据` })).toHaveAttribute('href', `/investment-monitor/feed?event=${id}`);
    }
    fireEvent.click(screen.getByRole('button', { name: '查看全部新闻' }));
    expect(screen.getByLabelText('当前路由')).toHaveTextContent('/investment-monitor/feed?channel=news');
  });

  it.each([
    ['603306.SH', '300308.SZ', '不同公司'],
    ['603306.SH', '603306.SZ', '同号不同后缀交易所'],
    ['603306.SH', 'SZ603306', '同号不同前缀交易所'],
    ['SZ.000001', '000001.SH', '股票与上证指数同号'],
    ['920748.BJ', 'SH920748', '北交所与沪市同号'],
    ['603306.SH', '', '缺少身份'],
    ['603306.SH', '   ', '空白身份'],
    ['BRK.A', 'BRK.B', '美股不同类别'],
    ['005930.KS', '005930.KQ', '韩国不同交易所'],
  ])('拒绝错误实时行：%s / %s（%s），保留整份原报价', async (symbol, stockCode) => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: dashboard('账号 A 股票', symbol) });
    mockLiveQuotes([[quoteKey(symbol), liveQuote(stockCode)]]);
    render(fixture()); await flush();
    expectFocusedQuote('10.00', '+1.00%', '2026-09-11 15:00:00', 'test');
    const detail = screen.getByRole('heading', { name: '账号 A 股票' }).closest('aside')!;
    expect(detail).not.toHaveTextContent('8.00');
    expect(detail).not.toHaveTextContent('腾讯实时行情');
    expect(screen.getByTestId(`chart-${symbol}`)).toBeInTheDocument();
  });

  it.each([
    ['603306.SH', '603306.SH'], ['603306.SH', ' 603306.sh '],
    ['603306.SH', '603306'], ['603306', '603306.SH'],
    ['603306.SH', 'SH603306'], ['603306.SH', 'SH.603306'],
    ['603306.SH', '603306.SS'], ['920748.BJ', 'BJ920748'],
    ['00700.HK', 'HK00700'], ['AAPL', 'aapl'],
  ])('兼容相同证券表示：%s / %s，切换卡与详情同步更新', async (symbol, stockCode) => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: dashboard('账号 A 股票', symbol) });
    mockLiveQuotes([[quoteKey(symbol), liveQuote(stockCode)]]);
    render(fixture()); await flush();
    expectFocusedQuote('8.00', '-20.00%', '2026-09-11 15:01:00', '腾讯实时行情');
    expect(screen.getByTestId(`chart-${symbol}`)).toBeInTheDocument();
  });

  it.each([
    ['2026-09-11T14:30:00', '2026-09-11T15:00:00', true, true, '2026-09-11 15:00:00'],
    ['2026-09-11T14:30:00', '20260911', true, true, '2026-09-11'],
    ['2026-09-11T14:30:00', '2026-09-11T14:29:59', false, false, '2026-09-11 14:30:00'],
    ['20260911', '2026-09-11T14:59:59', false, false, '2026-09-11'],
    ['2026-09-11T14:30:00', '2026-09-11T06:29:59Z', false, false, '2026-09-11 14:30:00'],
    ['2026-09-11T14:30:00', '2026-09-11T06:31:00Z', false, true, '2026-09-11 14:31:00'],
    ['2026-09-11T14:30:00', '2026-09-10T15:00:00', false, false, '2026-09-11 14:30:00'],
    ['2026-09-11T14:30:00', null, false, false, '2026-09-11 14:30:00'],
  ])('报价优先级 %s → %s（stale=%s）始终按时间替换整行', async (currentAt, candidateAt, isStale, accepted, labelTime) => {
    const value = dashboard(); value.watchlist[0].latestQuote!.updateTime = currentAt;
    vi.mocked(apiClient.get).mockResolvedValue({ data: value });
    mockLiveQuotes([[quoteKey('603306.SH'), liveQuote('603306.SH', { updateTime: candidateAt, isStale })]]);
    render(fixture()); await flush();
    expectFocusedQuote(accepted ? '8.00' : '10.00', accepted ? '-20.00%' : '+1.00%', labelTime, accepted ? '腾讯实时行情' : 'test');
  });

  it.each([
    ['2026-09-11T06:31:05Z', '2026-09-11 14:31:05'],
    ['2026-09-10T16:01:02Z', '2026-09-11 00:01:02'],
    ['2026-09-11T14:31:05+08:00', '2026-09-11 14:31:05'],
    ['2026-09-11T02:31:05-04:00', '2026-09-11 14:31:05'],
    ['2026-09-11T14:31:05', '2026-09-11 14:31:05'],
    ['2026-09-11 14:31:05', '2026-09-11 14:31:05'],
    ['2026/09/11 14:31:05', '2026-09-11 14:31:05'],
    ['20260911T06:31:05Z', '2026-09-11 14:31:05'],
    ['2026-09-11T06:31Z', '2026-09-11 14:31'],
    ['2026-09-11', '2026-09-11'], ['20260911', '2026-09-11'],
    ['2026/09/11', '2026-09-11'],
    ['', '时间未标注'], ['not-a-date', '时间未标注'],
  ])('首页报价和总览时间显示：%s → %s，不依赖浏览器本地时区', async (input, expected) => {
    const value = dashboard();
    value.marketTime = input; value.watchlist[0].latestQuote!.updateTime = input;
    vi.mocked(apiClient.get).mockResolvedValue({ data: value });
    render(fixture()); await flush();
    expectFocusedQuote('10.00', '+1.00%', expected, 'test');
    expect(within(screen.getByRole('region', { name: '数据证据轨道' })).getByText(expected)).toBeInTheDocument();
    if (input) expect(screen.getByText(text => text.startsWith(`${expected} · 自选与大盘`))).toBeInTheDocument();
  });

  it('同号不同交易所切换时，不把指数报价覆盖股票或沿用前一个证券的时间涨跌', async () => {
    const value = dashboard('上证指数自选', '000001.SH');
    value.watchlist.push(card('000001.SZ', '平安银行自选'));
    vi.mocked(apiClient.get).mockResolvedValue({ data: value });
    mockLiveQuotes([[quoteKey('000001.SH'), liveQuote('000001.SH')]]);
    render(fixture()); await flush();
    expectFocusedQuote('8.00', '-20.00%', '2026-09-11 15:01:00', '腾讯实时行情', '上证指数自选');
    fireEvent.click(screen.getByRole('button', { name: /^平安银行自选/ }));
    expectFocusedQuote('10.00', '+1.00%', '2026-09-11 15:00:00', 'test', '平安银行自选');
    expect(screen.getByTestId('chart-000001.SZ')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^上证指数自选/ })).toHaveTextContent('8.00 -20.00%');
  });
});
