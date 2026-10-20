import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jevLiveTApi } from '../../api/jevLiveT';
import type { LiveTContext, LiveTRun, LiveTStatus } from '../../types/jevLiveT';
import JevLiveTPage from '../JevLiveTPage';
import JevBacktestPage from '../JevBacktestPage';

const access = { user: { id: 1, status: 'approved' }, authMethod: 'user', accessEnabled: true, loggedIn: true, isLoading: false };
vi.mock('../../contexts/UserAccessContext', () => ({ useUserAccess: () => access }));
vi.mock('../../api/jevLiveT', () => ({ jevLiveTApi: { status: vi.fn(), runs: vi.fn(), run: vi.fn(), control: vi.fn(), setControl: vi.fn() } }));
const run: LiveTRun = {
  runId: 'live-one', status: 'stopped', message: '有界纸上试跑已停止', createdAt: '2026-09-21T10:00:00+08:00', updatedAt: '2026-09-21T10:03:00+08:00', stopReason: '达到本轮时长上限',
  config: { symbol: '603306', name: '华懋科技', currency: 'CNY', settlement: 't1', initialBaseValue: 8000000, initialCash: 4000000, quotePollIntervalMs: 1000, decisionIntervalSeconds: 30, durationSeconds: 180, maxModelRequests: 6, maxCostUsd: .01 },
  quote: { marketTs: '2026-09-21T10:02:55+08:00', receivedAt: '2026-09-21T10:03:00+08:00', ageMs: 5000, source: 'Tencent public snapshot', price: 40, observedUpdateIntervalMs: 3000, duplicateCount: 3, stale: true },
  account: { initialEquity: 12000000, currentEquity: 11999950, holdBaselineEquity: 12000000, totalPnl: -50, incrementalPnl: -50, initialShares: 200000, totalShares: 200000, sellableYesterdayShares: 199900, todayFrozenShares: 100, cash: 3999950, inventoryGapShares: 0, closedTNetPnl: -25, costReductionPerInitialShare: -.000125, totalFees: 25 },
  usage: { requests: 2, cached: 0, costUsd: .0002, lastLatencyMs: 3100, medianLatencyMs: 2900 },
  equity: [{ timestamp: '2026-09-21T10:00:00+08:00', equity: 12000000, holdBaselineEquity: 12000000 }, { timestamp: '2026-09-21T10:03:00+08:00', equity: 11999950, holdBaselineEquity: 12000000 }],
  signals: [{ marketTs: '2026-09-21T10:02:55+08:00', receivedAt: '2026-09-21T10:03:00+08:00', action: 'BUY', confidence: .8, reason: '模拟回补', executionStatus: '已模拟成交' }],
  trades: [{ timestamp: '2026-09-21T10:03:00+08:00', marketTs: '2026-09-21T10:02:55+08:00', action: 'BUY', shares: 100, price: 40, fee: 5, cashAfter: 3999950, sellableAfter: 199900, frozenAfter: 100, reason: '买入进入今日冻结库存' }],
  warnings: ['公开快照约滞后5秒，模拟成交不是券商真实成交。'], methodology: ['严格T+1，今日买入不可当日再卖。'],
};
const status: LiveTStatus = { available: true, message: '授权管理员试跑', serverTime: '2026-09-21T10:03:00+08:00', pollIntervalMs: 2000, currentRun: run };
const context: LiveTContext = {
  hs300: { status: 'fresh', source: 'sina', sourceTime: '2026-09-21T05:00:00Z', availableAt: '2026-09-21T05:00:02Z', ageSeconds: 2, values: { returnSincePrevClose: .01, return30S: -.0002, return60S: 0 } },
  zz1000: { status: 'error', source: 'sina', sourceTime: null, availableAt: null, ageSeconds: null, values: {} },
  cpo: { status: 'stale', source: 'member_snapshots', sourceTime: '2026-09-21T04:58:00Z', availableAt: '2026-09-21T04:58:02Z', ageSeconds: 122, values: { memberCount: 3, coverageRatio: .6 } },
  news: { status: 'fresh', source: '公开新闻源', fetchedAt: '2026-09-21T05:00:00Z', items: [{ title: '公开快讯标题', summary: '公开摘要', source: '新闻来源', url: 'https://example.com/news', publishedAt: '2026-09-21T04:00:00Z', availableAt: '2026-09-21T05:00:00Z' }] },
};
function LocationProbe() { return <div aria-label="试跑地址">{useLocation().search}</div>; }
function Page({ url = '/jev-backtest?lab=live-t', tabs = false }: { url?: string; tabs?: boolean }) { return <MemoryRouter initialEntries={[url]}>{tabs ? <JevBacktestPage /> : <JevLiveTPage />}<LocationProbe /></MemoryRouter>; }

beforeEach(() => {
  vi.resetAllMocks(); access.user = { id: 1, status: 'approved' }; access.loggedIn = true; access.isLoading = false;
  vi.mocked(jevLiveTApi.control).mockResolvedValue({ canControl: false, enabled: false, phase: 'stopped', runId: null, config: null, usage: null, message: '当前账号无持续控制权限', updatedAt: null });
  vi.mocked(jevLiveTApi.status).mockResolvedValue(status);
  vi.mocked(jevLiveTApi.runs).mockResolvedValue({ items: [run] });
  vi.mocked(jevLiveTApi.run).mockResolvedValue(run);
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe('read-only live paper T observer', () => {
  it('labels retained signal and trade windows as recent rather than lifetime totals', async () => {
    vi.mocked(jevLiveTApi.status).mockResolvedValue({ ...status, currentRun: { ...run, usage: { ...run.usage, costIsEstimate: true } } });
    render(<Page />);
    await screen.findByText('最近 1 笔 · 没有券商下单');
    expect(screen.getByText('最近 1 次')).toBeInTheDocument();
    expect(screen.getByText('本记录未核验预算估值 / 可用额度（美元）').parentElement).toHaveTextContent('$0.0002 / $0.0100');
    expect(screen.getByText('近期权益走势；累计盈亏以账户统计为准。')).toBeInTheDocument();
  });
  it('moves from a historical URL to the confirmed continuous account after manual start', async () => {
    const control = { canControl: true, enabled: false, phase: 'stopped' as const, runId: null, config: { maxCostUsd: 10 }, usage: { costUsd: .008 }, message: '尚未开启', updatedAt: null };
    vi.mocked(jevLiveTApi.control).mockResolvedValue(control);
    vi.mocked(jevLiveTApi.setControl).mockResolvedValue({ ...control, enabled: true, phase: 'starting', runId: 'continuous-new' });
    vi.mocked(jevLiveTApi.run).mockImplementation(async id => ({ ...run, runId: id }));
    render(<Page url="/jev-backtest?lab=live-t&run=old-history" />);
    fireEvent.click(await screen.findByRole('button', { name: '开启持续纸上运行' }));
    await waitFor(() => expect(screen.getByLabelText('试跑地址')).toHaveTextContent('run=continuous-new'));
    expect(jevLiveTApi.run).toHaveBeenCalledWith('continuous-new', expect.any(AbortSignal));
    expect(jevLiveTApi.setControl).toHaveBeenCalledTimes(1);
  });
  it('labels an unlimited continuous record without inventing limits for legacy unknown values', async () => {
    vi.mocked(jevLiveTApi.status).mockResolvedValue({ ...status, currentRun: { ...run, config: { ...run.config, durationSeconds: null, maxModelRequests: null } } });
    render(<Page />);
    await screen.findByRole('heading', { name: '华懋科技 · 已终止' });
    expect(screen.getByText('已调用 / 本轮上限').parentElement).toHaveTextContent('2 / 不限');
    expect(screen.getByText('本轮最长时长').parentElement).toHaveTextContent('持续');
  });
  it('shows model direction and confidence sizing separately from actual fills without a threshold', async () => {
    vi.mocked(jevLiveTApi.status).mockResolvedValue({ ...status, currentRun: { ...run, config: { ...run.config, confidenceSizing: true, confidenceThreshold: .65, lotSize: 100, orderQuantity: 1000 }, signals: [{ ...run.signals[0], confidence: .5, requestedQuantity: 500 }] } });
    render(<Page />);
    await screen.findByText('模型决定方向 · 置信度决定数量');
    expect(screen.getByText(/买卖请求最低 100 股，最高 1,000 股/)).toHaveTextContent('50% = 500 股');
    expect(screen.getByText(/买卖请求最低/)).toHaveTextContent('观望不产生委托；实际成交仍受可售库存、资金、流动性及时效约束');
    expect(screen.queryByText(/执行置信度门槛/)).not.toBeInTheDocument();
    const signalTable = screen.getByRole('columnheader', { name: '请求股数' }).closest('table')!;
    expect(within(signalTable).getByRole('cell', { name: '500' })).toBeInTheDocument();
    const trades = screen.getByRole('region', { name: '实时纸上模拟成交' });
    expect(within(trades).getByRole('cell', { name: '100' })).toBeInTheDocument();
  });
  it('keeps the stored legacy threshold and never labels it as confidence sizing', async () => {
    vi.mocked(jevLiveTApi.status).mockResolvedValue({ ...status, currentRun: { ...run, config: { ...run.config, confidenceSizing: false, confidenceThreshold: .65 } } });
    render(<Page />);
    await screen.findByText('历史执行置信度门槛：65.00%');
    expect(screen.queryByText('模型决定方向 · 置信度决定数量')).not.toBeInTheDocument();
  });
  it('uses configured quantity limits and leaves absent requested quantity unknown', async () => {
    vi.mocked(jevLiveTApi.status).mockResolvedValue({ ...status, currentRun: { ...run, config: { ...run.config, confidenceSizing: true, lotSize: 100, orderQuantity: 600 } } });
    render(<Page />);
    await screen.findByText(/买卖请求最低 100 股，最高 600 股/);
    expect(screen.getByText(/买卖请求最低/)).toHaveTextContent('50% = 300 股');
    const cells = within(screen.getByRole('columnheader', { name: '请求股数' }).closest('table')!).getAllByRole('cell');
    expect(cells[4]).toHaveTextContent('—');
  });
  it('filters null-like diagnostics instead of showing literal None beside live sources', async () => {
    vi.mocked(jevLiveTApi.status).mockResolvedValue({ ...status, currentRun: { ...run, context, contextDiagnostics: { cpo: { label: 'None', reason: ' None ', sourceKind: 'proxy' }, news: { label: '公开快讯', reason: 'None', sourceKind: 'public' } } } });
    render(<Page />);
    await screen.findByRole('heading', { name: 'CPO 成分股等权参考' });
    expect(screen.getByText('成分股等权参考，不是官方板块指数。')).toBeInTheDocument();
    expect(screen.queryByText(/None/)).not.toBeInTheDocument();
  });
  it('explains that missing required context stopped the run before any model call', async () => {
    vi.mocked(jevLiveTApi.status).mockResolvedValue({ ...status, currentRun: { ...run, stopReason: 'context_not_ready', usage: { requests: 0, cached: 0, costUsd: 0 } } });
    render(<Page />);
    await screen.findByText('停止原因：本轮未取得全部有效上下文，未调用模型');
  });
  it('separates polling cadence, actual snapshot interval, latency and T+1 inventory', async () => {
    render(<Page />);
    await screen.findByRole('heading', { name: '华懋科技 · 已终止' });
    expect(screen.getByText('行情已滞后')).toBeInTheDocument();
    expect(screen.getByText('Tencent public snapshot')).toBeInTheDocument();
    expect(screen.getByText('采集轮询间隔').parentElement).toHaveTextContent('1.00 秒');
    expect(screen.getByText('行情实际更新间隔').parentElement).toHaveTextContent('3.00 秒');
    expect(screen.getByText('快照时行情年龄').parentElement).toHaveTextContent('5.00 秒');
    expect(screen.getByText('昨日可售库存（股）').parentElement).toHaveTextContent('199,900');
    expect(screen.getByText('今日冻结库存（股）').parentElement).toHaveTextContent('100');
    expect(screen.getByText('相对持有的增量收益（CNY）').parentElement).toHaveTextContent('-50.00');
    expect(screen.getByText('已闭合模拟T净利润（CNY）').parentElement).toHaveTextContent('-25.00');
    expect(screen.getByText(/不代表每秒都有交易所新行情/)).toBeInTheDocument();
    expect(screen.getByText(/不是实际买入成本|不是真实买入成本/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /下单|开始|生成|暂停|终止/ })).not.toBeInTheDocument();
  });
  it('separates the target check cadence from actual dispatch intervals and HTTP response time', async () => {
    vi.mocked(jevLiveTApi.status).mockResolvedValue({ ...status, currentRun: { ...run,
      config: { ...run.config, decisionIntervalSeconds: 1 },
      usage: { ...run.usage, observedDecisionIntervalMs: 11250, lastLatencyMs: 1240, medianLatencyMs: 1360,
        lastModelRequestAt: '2026-09-21T05:57:04Z', lastModelResponseAt: '2026-09-21T13:57:05.240+08:00' },
    } });
    render(<Page />);
    await screen.findByRole('heading', { name: '华懋科技 · 已终止' });
    expect(screen.getByText('目标判断检查间隔').parentElement).toHaveTextContent('1.00 秒');
    expect(screen.getByText('行情实际更新间隔').parentElement).toHaveTextContent('3.00 秒');
    expect(screen.getByText('实际模型请求间隔（近期中位数）').parentElement).toHaveTextContent('11.25 秒');
    expect(screen.getByText('模型 HTTP 响应（最近）').parentElement).toHaveTextContent('1.24 秒');
    expect(screen.getByText('模型 HTTP 响应（中位数）').parentElement).toHaveTextContent('1.36 秒');
    expect(screen.getByText('最近模型请求发出').parentElement).toHaveTextContent('2026-09-21 13:57:04');
    expect(screen.getByText('最近模型响应收到').parentElement).toHaveTextContent('2026-09-21 13:57:05');
    expect(screen.getByText(/目标检查间隔不代表每次都调用模型/)).toHaveTextContent('暂停和重启空档不计入');
  });
  it('does not substitute target cadence for missing actual measurements or invent timestamp zones', async () => {
    vi.mocked(jevLiveTApi.status).mockResolvedValue({ ...status, currentRun: { ...run,
      config: { ...run.config, decisionIntervalSeconds: 1 },
      quote: { ...run.quote, observedUpdateIntervalMs: null },
      usage: { requests: 1, cached: 0, costUsd: 0, observedDecisionIntervalMs: null,
        lastModelRequestAt: '2026-09-21T13:57:04', lastModelResponseAt: 'invalid' },
    } });
    render(<Page />);
    await screen.findByRole('heading', { name: '华懋科技 · 已终止' });
    for (const label of ['行情实际更新间隔', '实际模型请求间隔（近期中位数）', '模型 HTTP 响应（最近）', '模型 HTTP 响应（中位数）', '最近模型请求发出', '最近模型响应收到']) {
      expect(screen.getByText(label).nextElementSibling).toHaveTextContent(/^—$/);
    }
    expect(screen.getByText('目标判断检查间隔').parentElement).toHaveTextContent('1.00 秒');
  });
  it('leaves invalid timing measurements unknown while preserving a measured zero', async () => {
    vi.mocked(jevLiveTApi.status).mockResolvedValue({ ...status, currentRun: { ...run,
      usage: { ...run.usage, observedDecisionIntervalMs: Number.NaN, lastLatencyMs: -1, medianLatencyMs: 0 },
    } });
    render(<Page />);
    await screen.findByRole('heading', { name: '华懋科技 · 已终止' });
    expect(screen.getByText('实际模型请求间隔（近期中位数）').nextElementSibling).toHaveTextContent(/^—$/);
    expect(screen.getByText('模型 HTTP 响应（最近）').nextElementSibling).toHaveTextContent(/^—$/);
    expect(screen.getByText('模型 HTTP 响应（中位数）').parentElement).toHaveTextContent('0.00 秒');
  });
  it('shows an empty authorized state without fabricated account values or returns', async () => {
    vi.mocked(jevLiveTApi.status).mockResolvedValue({ available: false, message: '当前账号尚未绑定试跑', currentRun: null });
    vi.mocked(jevLiveTApi.runs).mockResolvedValue({ items: [] });
    render(<Page />);
    await screen.findByText('尚无可查看的授权试跑');
    expect(screen.queryByText('当前账户权益（CNY）')).not.toBeInTheDocument();
    expect(screen.queryByText('0.00')).not.toBeInTheDocument();
    expect(jevLiveTApi.run).not.toHaveBeenCalled();
  });
  it('lightly polls the latest page after a run stops and discovers the next trial', async () => {
    vi.useFakeTimers();
    vi.mocked(jevLiveTApi.status).mockResolvedValueOnce({ ...status, currentRun: { ...run, status: 'running', stopReason: null } }).mockResolvedValueOnce(status).mockResolvedValue({ ...status, currentRun: { ...run, runId: 'live-next', status: 'starting', stopReason: null } });
    await act(async () => { render(<Page />); });
    expect(jevLiveTApi.status).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(jevLiveTApi.status).toHaveBeenCalledTimes(2);
    expect(screen.getByText(/达到本轮时长上限/)).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(9999); });
    expect(jevLiveTApi.status).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(jevLiveTApi.status).toHaveBeenCalledTimes(3);
    expect(screen.getByRole('heading', { name: '华懋科技 · 准备中' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '查看试跑 live-nex' })).toBeInTheDocument();
    expect(jevLiveTApi.runs).toHaveBeenCalledTimes(1);
  });
  it('does not poll a completed explicit historical run to discover unrelated trials', async () => {
    vi.useFakeTimers();
    await act(async () => { render(<Page url="/jev-backtest?lab=live-t&run=live-one" />); });
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(jevLiveTApi.status).toHaveBeenCalledTimes(1);
    expect(jevLiveTApi.run).toHaveBeenCalledTimes(1);
  });
  it('continues light discovery when there is no run yet', async () => {
    vi.useFakeTimers();
    vi.mocked(jevLiveTApi.status).mockResolvedValueOnce({ available: false, message: '等待新试跑', currentRun: null }).mockResolvedValue(status);
    vi.mocked(jevLiveTApi.runs).mockResolvedValue({ items: [] });
    await act(async () => { render(<Page />); });
    expect(screen.getByText('尚无可查看的授权试跑')).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(screen.getByRole('heading', { name: '华懋科技 · 已终止' })).toBeInTheDocument();
  });
  it('normalizes UTC and offset timestamps to Shanghai without guessing missing zones', async () => {
    vi.mocked(jevLiveTApi.status).mockResolvedValue({ ...status, currentRun: { ...run, updatedAt: '2026-09-21T02:03:00Z', quote: { ...run.quote, marketTs: '2026-09-21T10:02:55+08:00', receivedAt: '2026-09-21T02:03:00Z' }, signals: [{ marketTs: '2026-09-21T02:02:55Z', receivedAt: '2026-09-21T02:03:01Z', action: 'HOLD' }], trades: [], equity: [] } });
    render(<Page />);
    await screen.findByRole('heading', { name: '华懋科技 · 已终止' });
    expect(screen.getByText('接收时间 received_at').parentElement).toHaveTextContent('2026-09-21 10:03:00');
    expect(screen.getByText('行情时间 market_ts').parentElement).toHaveTextContent('2026-09-21 10:02:55');
    expect(screen.getByText('2026-09-21 10:03:01')).toBeInTheDocument();
    expect(screen.getByText('时间均为上海时间（UTC+8）')).toBeInTheDocument();
    expect(screen.queryByText(/2026-09-21 02:03/)).not.toBeInTheDocument();
  });
  it('distinguishes newly collected factors from the exact last model context and preserves missing values', async () => {
    const modelContext = { ...context, hs300: { ...context.hs300, values: { returnSincePrevClose: -.02 } } };
    vi.mocked(jevLiveTApi.status).mockResolvedValue({ ...status, currentRun: { ...run, context, lastModelContext: { decisionAt: '2026-09-21T05:00:03Z', context: modelContext } } });
    render(<Page />);
    await screen.findByRole('heading', { name: '市场与公开快讯' });
    const collected = within(screen.getByLabelText('当前已收集的信息'));
    const hs300 = within(collected.getByRole('article', { name: '沪深300' }));
    expect(hs300.getByText('较昨收').parentElement).toHaveTextContent('1.00%');
    expect(hs300.getByText('近30秒变化').parentElement).toHaveTextContent('-0.02%');
    expect(hs300.getByText('近60秒变化').parentElement).toHaveTextContent('0.00%');
    expect(hs300.getByText('实际可用时间').parentElement).toHaveTextContent('2026-09-21 13:00:02');
    const missing = within(collected.getByRole('article', { name: '中证1000' }));
    expect(missing.getByText('获取失败')).toBeInTheDocument();
    expect(missing.getByText('较昨收').parentElement).toHaveTextContent('—');
    const cpo = within(collected.getByRole('article', { name: 'CPO 成分股等权参考' }));
    expect(cpo.getByText('数据覆盖度').parentElement).toHaveTextContent('60.00%');
    expect(cpo.getByText(/不是官方板块指数/)).toBeInTheDocument();
    expect(cpo.getByText('已滞后')).toBeInTheDocument();
    fireEvent.click(screen.getByText('最近实际提交模型的上下文'));
    const submitted = within(screen.getByLabelText('最近实际提交模型的信息'));
    expect(within(submitted.getByRole('article', { name: '沪深300' })).getByText('较昨收').parentElement).toHaveTextContent('-2.00%');
    expect(screen.getByText(/提交时间 2026-09-21 13:00:03/)).toBeInTheDocument();
  });
  it('renders news as text and only exposes credential-free HTTPS links', async () => {
    const item = context.news.items[0];
    const news = { ...context.news, items: [
      { ...item, title: '<img src=x onerror=alert(1)>', summary: '<script>不执行</script>', url: 'javascript:alert(1)' },
      { ...item, title: 'HTTP链接保留为文本', url: 'http://example.com/news' },
      { ...item, title: '带凭据链接保留为文本', url: 'https://user:secret@example.com/news' },
      { ...item, title: '安全公开新闻', url: 'https://example.com/safe' },
    ] };
    vi.mocked(jevLiveTApi.status).mockResolvedValue({ ...status, currentRun: { ...run, context: { ...context, news } } });
    render(<Page />);
    const list = await screen.findByLabelText('公开快讯列表');
    expect(within(list).getByText('<img src=x onerror=alert(1)>')).toBeInTheDocument();
    expect(within(list).getByText('<script>不执行</script>')).toBeInTheDocument();
    expect(list.querySelector('img,script')).toBeNull();
    expect(within(list).getAllByRole('link')).toHaveLength(1);
    expect(within(list).getByRole('link', { name: '安全公开新闻' })).toHaveAttribute('href', 'https://example.com/safe');
    expect(within(list).getByRole('link', { name: '安全公开新闻' })).toHaveAttribute('rel', 'noopener noreferrer');
    expect(list).toHaveTextContent('发布时间 2026-09-21 12:00:00');
  });
  it('keeps old context-free records explicit and displays the actual budget separately from spend', async () => {
    vi.mocked(jevLiveTApi.status).mockResolvedValue({ ...status, currentRun: { ...run, config: { ...run.config, maxCostUsd: 10 }, usage: { ...run.usage, costUsd: .0012 } } });
    render(<Page />);
    await screen.findByText('本轮未启用或未返回扩展上下文。');
    expect(screen.getByText('本记录模型费用 / 可用额度（美元）').parentElement).toHaveTextContent('$0.0012 / $10.0000');
    fireEvent.click(screen.getByText('最近实际提交模型的上下文'));
    expect(screen.getByText('未返回已提交模型的扩展上下文记录。')).toBeInTheDocument();
  });
  it('continues two-second reads while paused and reflects a recovered heartbeat', async () => {
    vi.useFakeTimers();
    vi.mocked(jevLiveTApi.status).mockResolvedValueOnce({ ...status, currentRun: { ...run, status: 'paused', message: '心跳中断' } }).mockResolvedValueOnce({ ...status, currentRun: { ...run, status: 'running' } }).mockResolvedValue(status);
    await act(async () => { render(<Page />); });
    expect(screen.getByRole('heading', { name: '华懋科技 · 已暂停' })).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(screen.getByRole('heading', { name: '华懋科技 · 观测中' })).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(screen.getByRole('heading', { name: '华懋科技 · 已终止' })).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(jevLiveTApi.status).toHaveBeenCalledTimes(3);
  });
  it('plots only returned incremental PnL on a separate scale with a zero baseline', async () => {
    vi.mocked(jevLiveTApi.status).mockResolvedValue({ ...status, currentRun: { ...run, equity: [
      { timestamp: '2026-09-21T10:00:00+08:00', equity: 12000000, holdBaselineEquity: 12000000, incrementalPnl: -20 },
      { timestamp: '2026-09-21T10:01:00+08:00', equity: 12000000, holdBaselineEquity: 12000000, incrementalPnl: null },
      { timestamp: '2026-09-21T10:02:00+08:00', equity: 12000000, holdBaselineEquity: 12000000, incrementalPnl: 30 },
    ] } });
    render(<Page />);
    const chart = await screen.findByRole('img', { name: '做T相对持有增量收益曲线' });
    expect(chart.querySelectorAll('circle')).toHaveLength(2);
    expect(chart).toHaveTextContent('零收益基线');
    expect(chart).toHaveTextContent('增量收益 -20.00 CNY'); expect(chart).toHaveTextContent('增量收益 30.00 CNY');
    expect(chart.querySelector('path')?.getAttribute('d')?.match(/M/g)).toHaveLength(2);
    expect(chart).not.toHaveTextContent('12,000,000');
  });
  it('keeps missing account metrics unknown instead of inferring profit from cash', async () => {
    vi.mocked(jevLiveTApi.status).mockResolvedValue({ ...status, currentRun: { ...run, account: { cash: 5000000, inventoryGapShares: -100 }, quote: { ...run.quote, volume: 123456, volumeUnit: '股（当日累计）' } } });
    render(<Page />);
    await screen.findByRole('heading', { name: '华懋科技 · 已终止' });
    expect(screen.getByText('已闭合模拟T净利润（CNY）').parentElement).toHaveTextContent('—');
    expect(screen.getByText('相对持有的增量收益（CNY）').parentElement).toHaveTextContent('—');
    expect(screen.getByText('库存偏离初始（股）').parentElement).toHaveTextContent('-100');
    expect(screen.getByText('快照成交量（股（当日累计））').parentElement).toHaveTextContent('123,456');
  });
  it('recovers from a transient read failure without losing the active snapshot', async () => {
    vi.useFakeTimers();
    vi.mocked(jevLiveTApi.status).mockResolvedValueOnce({ ...status, currentRun: { ...run, status: 'running' } }).mockRejectedValueOnce(new Error('temporary network failure')).mockResolvedValue(status);
    await act(async () => { render(<Page />); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(screen.getByRole('alert')).toHaveTextContent('状态读取失败');
    expect(screen.getByText('Tencent public snapshot')).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '华懋科技 · 已终止' })).toBeInTheDocument();
    expect(jevLiveTApi.status).toHaveBeenCalledTimes(3);
  });
  it('does not replace a missing selected run with another active run or poll it', async () => {
    vi.useFakeTimers();
    vi.mocked(jevLiveTApi.status).mockResolvedValue({ ...status, currentRun: { ...run, status: 'running' } });
    vi.mocked(jevLiveTApi.run).mockRejectedValue({ response: { status: 404 } });
    await act(async () => { render(<Page url="/jev-backtest?lab=live-t&run=missing-private" />); });
    expect(screen.queryByText('Tencent public snapshot')).not.toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(jevLiveTApi.run).toHaveBeenCalledTimes(1);
  });
  it('selects history with a run deep link and only performs fresh reads', async () => {
    render(<Page />);
    fireEvent.click(await screen.findByRole('button', { name: '查看试跑 live-one' }));
    await waitFor(() => expect(jevLiveTApi.run).toHaveBeenCalledWith('live-one', expect.any(AbortSignal)));
    expect(screen.getByLabelText('试跑地址')).toHaveTextContent('run=live-one');
    fireEvent.click(screen.getByRole('button', { name: '刷新状态' }));
    await waitFor(() => expect(jevLiveTApi.run).toHaveBeenCalledTimes(2));
  });
  it('clears all private run data on a polling 401 and aborts subsequent reads', async () => {
    vi.useFakeTimers();
    vi.mocked(jevLiveTApi.status).mockResolvedValueOnce({ ...status, currentRun: { ...run, status: 'running' } }).mockRejectedValue({ response: { status: 401 } });
    await act(async () => { render(<Page />); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(screen.getByRole('alert')).toHaveTextContent('当前账号的试跑数据已清除');
    expect(screen.queryByText('Tencent public snapshot')).not.toBeInTheDocument();
    expect(vi.mocked(jevLiveTApi.status).mock.calls[0][0]?.aborted).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(jevLiveTApi.status).toHaveBeenCalledTimes(2);
  });
  it('clears a selected snapshot immediately on 403 even when another read is still pending', async () => {
    vi.useFakeTimers();
    const running = { ...run, status: 'running' as const };
    vi.mocked(jevLiveTApi.status).mockResolvedValueOnce({ ...status, currentRun: running }).mockRejectedValue({ response: { status: 403 } });
    vi.mocked(jevLiveTApi.run).mockResolvedValueOnce(running).mockImplementation(() => new Promise(() => {}));
    await act(async () => { render(<Page url="/jev-backtest?lab=live-t&run=live-one" />); });
    expect(screen.getByText('Tencent public snapshot')).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(screen.getByRole('alert')).toHaveTextContent('当前账号的试跑数据已清除');
    expect(screen.queryByText('Tencent public snapshot')).not.toBeInTheDocument();
    expect(vi.mocked(jevLiveTApi.run).mock.calls[1][1]?.aborted).toBe(true);
  });
  it('aborts active polling on unmount and does not render a prior identity snapshot', async () => {
    vi.mocked(jevLiveTApi.status).mockResolvedValueOnce({ ...status, currentRun: { ...run, status: 'running' } }).mockImplementation(() => new Promise(() => {}));
    const page = render(<Page />);
    await screen.findByText('Tencent public snapshot');
    const firstSignal = vi.mocked(jevLiveTApi.status).mock.calls[0][0];
    access.user = { id: 2, status: 'approved' }; page.rerender(<Page />);
    expect(screen.queryByText('Tencent public snapshot')).not.toBeInTheDocument();
    expect(firstSignal?.aborted).toBe(true);
    page.unmount();
    expect(vi.mocked(jevLiveTApi.status).mock.calls[1][0]?.aborted).toBe(true);
  });
  it('does not read APIs before authentication is complete', () => {
    access.loggedIn = false; render(<Page />);
    expect(jevLiveTApi.status).not.toHaveBeenCalled();
    expect(jevLiveTApi.runs).not.toHaveBeenCalled();
  });
  it('selects the new tab without passing old research task identifiers to live APIs', async () => {
    render(<Page tabs url="/jev-backtest?lab=live-t&task=private-old&round=sample-old" />);
    await screen.findByRole('heading', { name: '华懋科技 · 已终止' });
    expect(screen.getByRole('button', { name: '实时纸上做T' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: '分钟行情回测' })).toHaveAttribute('aria-pressed', 'false');
    expect(jevLiveTApi.run).not.toHaveBeenCalled();
  });
});
