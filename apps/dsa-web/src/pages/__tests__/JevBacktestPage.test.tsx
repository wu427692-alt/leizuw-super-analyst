import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jevBacktestApi } from '../../api/jevBacktest';
import type { JevBacktestConfig, JevBacktestResult, JevBacktestSummary, JevBacktestTask, JevCapabilities } from '../../types/jevBacktest';
import JevBacktestPage from '../JevBacktestPage';

const access = { user: { id: 1, status: 'approved' }, authMethod: 'user', accessEnabled: true, loggedIn: true, isLoading: false };
vi.mock('../../contexts/UserAccessContext', () => ({ useUserAccess: () => access }));
vi.mock('../../api/jevBacktest', () => ({ jevBacktestApi: { capabilities: vi.fn(), tasks: vi.fn(), task: vi.fn(), start: vi.fn(), cancel: vi.fn() } }));

const config: JevBacktestConfig = {
  dataSource: 'sina',
  symbol: '600000', startDate: '2026-09-17', endDate: '2026-09-18', timeframe: '1min',
  lookbackBars: 60, initialCash: 100000, confidenceThreshold: .65, commissionBps: 3,
  minCommission: 5, sellTaxBps: 5, slippageBps: 5, maxHoldingBars: 60, decisionInterval: 1,
  refreshPrices: true, maxCostUsd: .2, positionPct: 1, settlementMode: 't1', stopLossPct: 0, takeProfitPct: 0,
};
const capabilities: JevCapabilities = { configured: true, model: 'configured-test-model', provider: 'OpenRouter', timeframe: '1min', timeframes: ['1min', '5min', '15min'], limits: { maxEvaluationBars: 2000, maxCostUsd: .5 }, defaults: config, dataSources: ['测试行情源'] };
const summary: JevBacktestSummary = { initialCash: 100000, finalEquity: 100200, totalReturnPct: .2, maxDrawdownPct: 0, winRatePct: 100, tradeCount: 2, closedTradeCount: 1, totalCosts: 0, cash: 100200, positionShares: 0, positionMarketValue: 0, commission: 0, sellTax: 0, slippageCost: 0, modelCalls: 3, unavailableCount: 0, uncertainCount: 0, blockedOrderCount: 0, ruleExitCount: 0, warmupSkippedBars: 0 };
const equity = [
  { date: '2026-09-17T10:30:00', equity: 100000, cash: 100000, positionShares: 0, close: 10, drawdownPct: 0, realizedPnl: 0, unrealizedPnl: 0 },
  { date: '2026-09-17T10:31:00', equity: 100000, cash: 99000, positionShares: 100, close: 10, drawdownPct: 0, realizedPnl: 0, unrealizedPnl: 0 },
  { date: '2026-09-18T10:30:00', equity: 100200, cash: 99000, positionShares: 100, close: 12, drawdownPct: 0, realizedPnl: 0, unrealizedPnl: 200 },
  { date: '2026-09-18T10:31:00', equity: 100200, cash: 100200, positionShares: 0, close: 12, drawdownPct: 0, realizedPnl: 200, unrealizedPnl: 0 },
];
const result: JevBacktestResult = {
  summary, equity,
  signals: [{ date: equity[0].date, action: 'BUY', confidence: .8, status: 'ok', labels: ['测试量价信号'], executionAction: 'BUY', executionReason: '后续买入成交', fillStatus: 'filled', fillDate: equity[1].date }, { date: equity[2].date, action: 'SELL', confidence: .9, status: 'ok', labels: [], executionAction: 'SELL', executionReason: '未来卖出决策', fillStatus: 'filled', fillDate: equity[3].date }],
  trades: [{ date: equity[1].date, signalDate: equity[0].date, action: 'BUY', shares: 100, price: 10, gross: 1000, commission: 0, sellTax: 0, slippageCost: 0, cashAfter: 99000, positionAfter: 100, reason: '已成交买单' }, { date: equity[3].date, signalDate: equity[2].date, action: 'SELL', shares: 100, price: 12, gross: 1200, commission: 0, sellTax: 0, slippageCost: 0, cashAfter: 100200, positionAfter: 0, pnl: 200, reason: '未来卖出成交' }],
  baselines: { buyHold: { summary, equity, trades: [] }, ma520: { summary, equity, trades: [] } },
  dataQuality: { warnings: [] }, methodology: ['仅使用已知量价；下一根K线执行。'],
  marketData: { source: '测试分钟行情', frequency: '1min', bars: equity.map(row => ({ date: row.date, open: row.close, high: row.close + .1, low: row.close - .1, close: row.close, volume: 1000 })) },
};
function task(status: JevBacktestTask['status'] = 'completed', id = 'task-one'): JevBacktestTask {
  return { taskId: id, status, progress: status === 'completed' ? 100 : 10, message: status === 'running' ? '处理量价中' : '模拟完成', createdAt: '2026-09-20T00:00:00Z', config, usage: { requests: 3, cached: 0, costUsd: .0007 }, result: status === 'completed' ? result : null };
}
function LocationProbe() { const location = useLocation(); return <output aria-label="任务地址">{location.pathname}{location.search}</output>; }
function Page({ url = '/jev-backtest' }: { url?: string }) { return <MemoryRouter initialEntries={[url]}><JevBacktestPage /><LocationProbe /></MemoryRouter>; }

describe('Jev minute backtest workbench', () => {
  beforeEach(() => {
    vi.resetAllMocks(); access.user = { id: 1, status: 'approved' }; access.loggedIn = true;
    vi.mocked(jevBacktestApi.capabilities).mockResolvedValue(capabilities);
    vi.mocked(jevBacktestApi.tasks).mockResolvedValue({ items: [] });
    vi.mocked(jevBacktestApi.task).mockResolvedValue(task());
  });
  afterEach(() => { cleanup(); vi.useRealTimers(); });

  it('submits explicit paid minute settings and records the new task in the URL', async () => {
    const queued = task('queued'); vi.mocked(jevBacktestApi.start).mockResolvedValue(queued); vi.mocked(jevBacktestApi.task).mockResolvedValue(queued);
    render(<Page />);
    const start = await screen.findByRole('button', { name: '生成信号并回测' });
    await waitFor(() => expect(start).toBeEnabled());
    fireEvent.change(screen.getByLabelText('目标仓位（%）'), { target: { value: '50' } });
    fireEvent.change(screen.getByLabelText('交易制度'), { target: { value: 'research_t0' } });
    fireEvent.click(start);
    await waitFor(() => expect(jevBacktestApi.start).toHaveBeenCalledTimes(1));
    expect(jevBacktestApi.start).toHaveBeenCalledWith(expect.objectContaining({ dataSource: 'sina', symbol: '600000', timeframe: '1min', positionPct: .5, settlementMode: 'research_t0', maxCostUsd: .2 }), expect.stringMatching(/^[0-9a-f-]{36}$/), expect.any(AbortSignal));
    await waitFor(() => expect(screen.getByLabelText('任务地址')).toHaveTextContent('task=task-one'));
    expect(screen.getByText(/生成信号将调用付费 Jev/)).toBeInTheDocument();
  });

  it('reads history without spending and hides all future account movements until the cursor advances', async () => {
    const estimated = { ...task(), usage: { ...task().usage, costIsEstimate: true } };
    vi.mocked(jevBacktestApi.tasks).mockResolvedValue({ items: [estimated] });
    vi.mocked(jevBacktestApi.task).mockResolvedValue(estimated);
    render(<Page />);
    fireEvent.click(await screen.findByRole('button', { name: '查看 600000 任务 task-one' }));
    expect(await screen.findByRole('heading', { name: '600000 · 分钟行情回放' })).toBeInTheDocument();
    expect(screen.getByTestId('jev-current-profit')).toHaveTextContent('0.00');
    expect(screen.getByText('未核验预算估值 $0.0007')).toBeInTheDocument();
    expect(within(screen.getByRole('region', { name: '任务历史，可横向滚动' })).getByText('未核验预算估值')).toBeInTheDocument();
    expect(screen.queryByText('未来卖出成交')).not.toBeInTheDocument();
    expect(screen.queryByText('未来卖出决策')).not.toBeInTheDocument();
    expect(screen.queryByText('后续买入成交')).not.toBeInTheDocument();
    expect(screen.getAllByText('等待后续K线执行').length).toBeGreaterThan(0);
    fireEvent.change(screen.getByRole('slider', { name: '回放进度' }), { target: { value: '4' } });
    expect(screen.getByTestId('jev-current-profit')).toHaveTextContent('200.00');
    expect(screen.getByText('未来卖出成交')).toBeInTheDocument();
    expect(screen.getByLabelText('任务地址')).toHaveTextContent('?task=task-one');
    fireEvent.click(screen.getByRole('button', { name: '刷新历史' }));
    await waitFor(() => expect(jevBacktestApi.tasks).toHaveBeenCalledTimes(2));
    expect(jevBacktestApi.start).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '载入所选任务参数，修改后重跑' }));
    expect(screen.getByLabelText('获取最新历史行情')).not.toBeChecked();
  });

  it('keeps Tushare selectable at five-minute frequency and sends the selected source', async () => {
    const queued = task('queued'); vi.mocked(jevBacktestApi.start).mockResolvedValue(queued); vi.mocked(jevBacktestApi.task).mockResolvedValue(queued);
    render(<Page />);
    await waitFor(() => expect(screen.getByRole('button', { name: '生成信号并回测' })).toBeEnabled());
    expect(screen.getByLabelText('分钟数据源')).toHaveValue('sina');
    expect(screen.getByText(/保留来源原始成交量，模型只比较相对量/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('分钟数据源'), { target: { value: 'tushare' } });
    fireEvent.change(screen.getByLabelText('行情周期'), { target: { value: '5min' } });
    fireEvent.click(screen.getByRole('button', { name: '生成信号并回测' }));
    await waitFor(() => expect(jevBacktestApi.start).toHaveBeenCalledWith(expect.objectContaining({ dataSource: 'tushare', timeframe: '5min' }), expect.any(String), expect.any(AbortSignal)));
  });

  it('reveals real volume only as the replay cursor advances, including SVG tooltips', async () => {
    const volumes = [1234, 5678, 987654, 7654321];
    const withVolume = { ...task(), result: { ...result, marketData: { ...result.marketData!, bars: result.marketData!.bars.map((bar, index) => ({ ...bar, volume: volumes[index] })) } } };
    vi.mocked(jevBacktestApi.task).mockResolvedValue(withVolume);
    render(<Page url="/jev-backtest?task=task-one" />);
    const chart = await screen.findByRole('img', { name: '截至当前时点的真实分钟K线、原始成交量及实际成交点' });
    expect(within(chart).getByText('成交量（源原始单位）')).toBeInTheDocument();
    expect(within(chart).getByText('2026-09-17 10:30 原始成交量 1,234', { selector: 'title' })).toBeInTheDocument();
    expect(chart.textContent).not.toContain('987,654');
    expect(chart.textContent).not.toContain('7,654,321');
    fireEvent.change(screen.getByRole('slider', { name: '回放进度' }), { target: { value: '2' } });
    expect(within(chart).getByText('2026-09-17 10:31 原始成交量 5,678', { selector: 'title' })).toBeInTheDocument();
    expect(chart.textContent).not.toContain('987,654');
    fireEvent.change(screen.getByRole('slider', { name: '回放进度' }), { target: { value: '4' } });
    expect(within(chart).getByText('2026-09-18 10:31 原始成交量 7,654,321', { selector: 'title' })).toBeInTheDocument();
    fireEvent.change(screen.getByRole('slider', { name: '回放进度' }), { target: { value: '1' } });
    expect(chart.textContent).not.toContain('7,654,321');
    expect(chart.textContent).not.toContain('987,654');
    expect(jevBacktestApi.start).not.toHaveBeenCalled();
  });

  it('reports missing volume when only equity close prices were returned', async () => {
    vi.mocked(jevBacktestApi.task).mockResolvedValue({ ...task(), result: { ...result, marketData: undefined } });
    render(<Page url="/jev-backtest?task=task-one" />);
    const chart = await screen.findByRole('img', { name: '截至当前时点的分钟收盘价、原始成交量及实际成交点' });
    expect(within(chart).getByText('成交量未返回')).toBeInTheDocument();
    expect(within(chart).queryByLabelText('当前可见原始成交量')).not.toBeInTheDocument();
    expect(Array.from(chart.querySelectorAll('title')).filter(title => title.textContent?.includes('原始成交量'))).toHaveLength(0);
  });

  it('loads legacy task settings as Tushare instead of silently switching their source to the new default', async () => {
    const legacy = { ...task(), config: { ...config, dataSource: undefined } };
    vi.mocked(jevBacktestApi.tasks).mockResolvedValue({ items: [legacy] });
    vi.mocked(jevBacktestApi.task).mockResolvedValue(legacy);
    render(<Page url="/jev-backtest?task=task-one" />);
    await screen.findByLabelText('回放时点账户');
    expect(screen.getByLabelText('分钟数据源')).toHaveValue('sina');
    fireEvent.click(screen.getByRole('button', { name: '载入所选任务参数，修改后重跑' }));
    expect(screen.getByLabelText('分钟数据源')).toHaveValue('tushare');
    expect(screen.getByLabelText('获取最新历史行情')).not.toBeChecked();
    expect(screen.getAllByText('Tushare 分钟').length).toBeGreaterThan(0);
  });

  it('keeps failed tasks visibly failed without displaying a fabricated zero return', async () => {
    vi.mocked(jevBacktestApi.task).mockResolvedValue({ ...task('failed'), error: '模型预算已用尽' });
    render(<Page url="/jev-backtest?task=task-one" />);
    expect(await screen.findByText('回测失败：模型预算已用尽')).toBeInTheDocument();
    expect(screen.getByText('本次没有有效回测结果')).toBeInTheDocument();
    expect(screen.queryByLabelText('回放时点账户')).not.toBeInTheDocument();
    expect(screen.queryByText('0.00%')).not.toBeInTheDocument();
  });

  it('cancels the active task and aborts its old reader before polling again', async () => {
    const running = task('running'); const cancelled = task('cancelled');
    vi.mocked(jevBacktestApi.task).mockResolvedValue(running);
    vi.mocked(jevBacktestApi.cancel).mockResolvedValue(cancelled);
    render(<Page url="/jev-backtest?task=task-one" />);
    const cancel = await screen.findByRole('button', { name: '取消任务' });
    const readerSignal = vi.mocked(jevBacktestApi.task).mock.calls[0][1];
    vi.mocked(jevBacktestApi.task).mockResolvedValue(cancelled);
    fireEvent.click(cancel);
    await waitFor(() => expect(jevBacktestApi.cancel).toHaveBeenCalledWith('task-one', expect.any(AbortSignal)));
    expect(readerSignal?.aborted).toBe(true);
    expect(await screen.findByText('任务已取消，已发生的模型费用保留记录。')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '取消任务' })).not.toBeInTheDocument();
  });

  it('clears private history and results after a 401 response', async () => {
    vi.mocked(jevBacktestApi.tasks).mockResolvedValue({ items: [task()] });
    render(<Page url="/jev-backtest?task=task-one" />);
    await screen.findByLabelText('回放时点账户');
    vi.mocked(jevBacktestApi.tasks).mockRejectedValueOnce({ response: { status: 401, data: { detail: '会话过期' } } });
    fireEvent.click(screen.getByRole('button', { name: '刷新历史' }));
    expect(await screen.findByText(/当前账号的任务和结果已清除/)).toBeInTheDocument();
    expect(screen.queryByLabelText('回放时点账户')).not.toBeInTheDocument();
    expect(screen.queryByText('task-one')).not.toBeInTheDocument();
  });

  it('isolates account switches and ignores a late private response after unmount', async () => {
    let resolveOld!: (value: JevBacktestTask) => void;
    vi.mocked(jevBacktestApi.task).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }));
    const view = render(<Page url="/jev-backtest?task=task-one" />);
    await waitFor(() => expect(jevBacktestApi.task).toHaveBeenCalledTimes(1));
    const oldSignal = vi.mocked(jevBacktestApi.task).mock.calls[0][1];
    access.user = { id: 2, status: 'approved' };
    vi.mocked(jevBacktestApi.task).mockRejectedValueOnce({ response: { status: 404, data: { detail: '回测任务不存在' } } });
    view.rerender(<Page url="/jev-backtest?task=task-one" />);
    await act(async () => resolveOld(task()));
    expect(oldSignal?.aborted).toBe(true);
    expect(screen.queryByLabelText('回放时点账户')).not.toBeInTheDocument();
    expect(await screen.findByText(/状态读取失败/)).toBeInTheDocument();
  });

  it('shows only the current preview during a running task', async () => {
    vi.mocked(jevBacktestApi.task).mockResolvedValue({ ...task('running'), preview: { equity: equity.slice(0, 3), trades: result.trades.slice(0, 1), signals: result.signals.slice(0, 1), summary } });
    render(<Page url="/jev-backtest?task=task-one" />);
    const account = await screen.findByLabelText('回放时点账户');
    expect(within(account).getByTestId('jev-current-profit')).toHaveTextContent('200.00');
    expect(screen.getByRole('slider', { name: '回放进度' })).toBeDisabled();
    expect(screen.queryByText('未来卖出成交')).not.toBeInTheDocument();
  });

  it('allows a stopped partial preview to replay while keeping its incomplete status explicit', async () => {
    vi.mocked(jevBacktestApi.task).mockResolvedValue({ ...task('failed'), error: '模型预算已用尽', preview: { equity: equity.slice(0, 3), trades: result.trades.slice(0, 1), signals: result.signals.slice(0, 1), summary } });
    render(<Page url="/jev-backtest?task=task-one" />);
    expect(await screen.findByText('回测失败：模型预算已用尽')).toBeInTheDocument();
    expect(screen.getByText(/不是完整区间回测结果/)).toBeInTheDocument();
    expect(screen.getByRole('slider', { name: '回放进度' })).toBeEnabled();
    fireEvent.change(screen.getByRole('slider', { name: '回放进度' }), { target: { value: '3' } });
    expect(screen.getByTestId('jev-current-profit')).toHaveTextContent('200.00');
  });

  it('plays, pauses, and restarts at the end without creating another paid task', async () => {
    render(<Page url="/jev-backtest?task=task-one" />);
    await screen.findByLabelText('回放时点账户');
    vi.useFakeTimers();
    fireEvent.change(screen.getByRole('combobox', { name: '回放速度' }), { target: { value: '1' } });
    fireEvent.click(screen.getByRole('button', { name: '播放回放' }));
    await act(async () => vi.advanceTimersByTime(1000));
    expect(screen.getByRole('slider', { name: '回放进度' })).toHaveValue('2');
    fireEvent.click(screen.getByRole('button', { name: '暂停回放' }));
    await act(async () => vi.advanceTimersByTime(2000));
    expect(screen.getByRole('slider', { name: '回放进度' })).toHaveValue('2');
    fireEvent.change(screen.getByRole('slider', { name: '回放进度' }), { target: { value: '4' } });
    fireEvent.click(screen.getByRole('button', { name: '播放回放' }));
    expect(screen.getByRole('slider', { name: '回放进度' })).toHaveValue('1');
    await act(async () => vi.advanceTimersByTime(1000));
    expect(screen.getByRole('slider', { name: '回放进度' })).toHaveValue('2');
    expect(jevBacktestApi.start).not.toHaveBeenCalled();
  });
});
