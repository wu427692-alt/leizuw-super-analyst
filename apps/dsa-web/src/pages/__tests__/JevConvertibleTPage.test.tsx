import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jevConvertibleTApi } from '../../api/jevConvertibleT';
import { jevBacktestApi } from '../../api/jevBacktest';
import type { ConvertibleAvailability, ConvertibleCapabilities, ConvertibleTConfig, ConvertibleTask, TradingResearch } from '../../types/jevConvertibleT';
import JevConvertibleTPage from '../JevConvertibleTPage';
import JevBacktestPage from '../JevBacktestPage';

const access = { user: { id: 1, status: 'approved' }, authMethod: 'user', accessEnabled: true, loggedIn: true, isLoading: false };
vi.mock('../../contexts/UserAccessContext', () => ({ useUserAccess: () => access }));
vi.mock('../../api/jevConvertibleT', () => ({ jevConvertibleTApi: { capabilities: vi.fn(), availability: vi.fn(), tasks: vi.fn(), research: vi.fn(), researchRound: vi.fn(), task: vi.fn(), start: vi.fn(), cancel: vi.fn() } }));
vi.mock('../../api/jevBacktest', () => ({ jevBacktestApi: { capabilities: vi.fn(), tasks: vi.fn(), task: vi.fn(), start: vi.fn(), cancel: vi.fn() } }));
const config: ConvertibleTConfig = { symbol: 'NVDA', datasetId: 'nvda-real', accountMode: 'unlevered_margin', startDate: '2025-09-16', endDate: '2025-09-16', basePositionValue: 200000, initialCash: 200000, eventGranularity: '1s', decisionIntervalSeconds: 60, lookbackEvents: 60, factors: ['ohlcv', 'orderbook'], orderQuantity: 100, latencyMs: 1000, participationRate: .1, commissionBps: 1, minCommission: 0, slippageBps: 2, restoreBaseAtClose: true, confidenceThreshold: .65, maxCostUsd: .2 };
const availability: ConvertibleAvailability = { status: 'available', message: '真实历史秒聚合，未作插值。', sources: [{ key: 'verified', label: '已核验事件文件', status: 'available', frequency: '1s', count: 23348, coverageStart: '2025-09-16T09:30:00-04:00', coverageEnd: '2025-09-16T15:59:59-04:00' }], availableGranularities: ['1s'], factorAvailability: { ohlcv: { available: true }, orderbook: { available: true }, stock: { available: false }, sector: { available: false }, market: { available: false }, conversion_premium: { available: false } } };
const capabilities: ConvertibleCapabilities = { configured: true, model: 'test-model', defaults: config, limits: { maxCostUsd: .5 }, dataAvailability: availability, instruments: [{ symbol: 'NVDA', name: 'NVIDIA', currency: 'USD', quantityUnit: 'shares', lotSize: 1, market: 'NASDAQ', datasetId: 'nvda-real', simulationAccount: 'unlevered_margin', defaultStartDate: '2025-09-16', defaultEndDate: '2025-09-16', status: 'available', granularity: '1s' }, { symbol: '113677', name: '华懋转债', currency: 'CNY', quantityUnit: 'bonds', market: 'SSE', lotSize: 10, simulationAccount: 'cash_t0', defaultStartDate: '2026-09-18', defaultEndDate: '2026-09-18', status: 'partial' }] };
const research: TradingResearch = { status: 'not_started', title: '多轮真实对照', description: '测试区间不参与调参。', rounds: [], conclusion: '尚无实测结论。', limitations: [] };
const first = '2025-09-16T09:31:00-04:00', last = '2025-09-16T09:31:01-04:00';
function task(status: ConvertibleTask['status'] = 'completed'): ConvertibleTask {
  return { taskId: 'event-task', status, progress: 100, message: '完成事件模拟', createdAt: '2026-09-20T01:00:00Z', config: { ...config, currency: 'USD', quantityUnit: 'shares' }, usage: { requests: 2, cached: 0, costUsd: .0004 }, result: status === 'completed' ? { summary: { totalPnl: 19, incrementalTPnl: -1 }, equity: [{ timestamp: first, eventIndex: 60, equity: 400000, holdBaselineEquity: 400000, cash: 200000, positionQuantity: 1000, price: 200, totalPnl: 0, incrementalTPnl: 0 }, { timestamp: last, eventIndex: 61, equity: 400019, holdBaselineEquity: 400020, cash: 200019, positionQuantity: 1000, price: 200, totalPnl: 19, incrementalTPnl: -1 }], events: [{ timestamp: first, eventIndex: 60, price: 200, volume: 100 }, { timestamp: last, eventIndex: 61, price: 200, volume: 987654321 }], trades: [{ timestamp: last, eventIndex: 61, side: 'SELL', quantity: 10, price: 200, fee: 1, cashAfter: 200019, positionAfter: 1000, reason: '后续事件成交' }], signals: [{ timestamp: last, eventIndex: 61, action: 'SELL', confidence: .9, reason: '后续事件决策' }] } : null };
}
function LocationProbe() { return <output aria-label="研究地址">{useLocation().search}</output>; }
function Page({ url = '/jev-backtest?lab=convertible-t', tabs = false }: { url?: string; tabs?: boolean }) { return <MemoryRouter initialEntries={[url]}>{tabs ? <JevBacktestPage /> : <JevConvertibleTPage />}<LocationProbe /></MemoryRouter>; }

beforeEach(() => {
  vi.resetAllMocks(); access.user = { id: 1, status: 'approved' }; access.loggedIn = true;
  vi.mocked(jevConvertibleTApi.capabilities).mockResolvedValue(capabilities);
  vi.mocked(jevConvertibleTApi.availability).mockResolvedValue(availability);
  vi.mocked(jevConvertibleTApi.tasks).mockResolvedValue({ items: [] });
  vi.mocked(jevConvertibleTApi.task).mockResolvedValue(task());
  vi.mocked(jevConvertibleTApi.researchRound).mockResolvedValue({ ...task(), visibility: 'public_sample' });
  vi.mocked(jevConvertibleTApi.research).mockResolvedValue(research);
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe('event-time research workbench', () => {
  it('keeps stale CB fragments visible with their real date and prevents a paid submission', async () => {
    vi.mocked(jevConvertibleTApi.capabilities).mockResolvedValue({ ...capabilities, defaults: { ...config, symbol: '113677', datasetId: undefined, accountMode: 'cash_t0' }, dataAvailability: { ...availability, status: 'partial', availableGranularities: [], message: '仅有旧采集碎片，无法进行秒级回测。', sources: [{ key: 'fragments', label: '债券采集快照', status: 'partial', rows: 441, coverageStart: '2026-08-20 14:50:00', coverageEnd: '2026-08-20 15:00:00', reason: '单位尚未核验' }] } });
    render(<Page />);
    expect(await screen.findByText('441 条')).toBeInTheDocument();
    expect(screen.getByText('2026-08-20 14:50:00')).toBeInTheDocument();
    const start = screen.getByRole('button', { name: '开始秒级回测（付费）' });
    expect(start).toBeDisabled(); fireEvent.click(start);
    expect(jevConvertibleTApi.start).not.toHaveBeenCalled();
    expect(screen.getByText('当前记账币种：人民币 / CNY')).toBeInTheDocument();
    expect(screen.queryByText('0.00')).not.toBeInTheDocument();
  });

  it('makes USD explicit, separates event precision from inference cadence, and submits only after data is ready', async () => {
    vi.mocked(jevConvertibleTApi.start).mockResolvedValue(task('queued'));
    render(<Page />);
    const start = await screen.findByRole('button', { name: '开始秒级回测（付费）' });
    await waitFor(() => expect(start).toBeEnabled());
    expect(screen.getByText('当前记账币种：美元 / USD')).toBeInTheDocument();
    expect(screen.getByLabelText('初始底仓市值（美元 / USD）')).toHaveValue(200000);
    expect(screen.getByLabelText('每笔数量（股，1 的整数倍）')).toHaveAttribute('step', '1');
    expect(screen.getByText(/不模拟现金账户结算限制/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('模型判断间隔（秒）'), { target: { value: '90' } });
    fireEvent.click(start);
    await waitFor(() => expect(jevConvertibleTApi.start).toHaveBeenCalledWith(expect.objectContaining({ symbol: 'NVDA', datasetId: 'nvda-real', accountMode: 'unlevered_margin', eventGranularity: '1s', decisionIntervalSeconds: 90, maxCostUsd: .2 }), expect.any(String), expect.any(AbortSignal)));
    await waitFor(() => expect(screen.getByLabelText('研究地址')).toHaveTextContent('task=event-task'));
  });

  it('invalidates availability on instrument or date changes and rechecks without calling the model', async () => {
    render(<Page />); const start = await screen.findByRole('button', { name: '开始秒级回测（付费）' });
    await waitFor(() => expect(start).toBeEnabled());
    fireEvent.change(screen.getByLabelText('研究标的'), { target: { value: '113677|' } });
    expect(start).toBeDisabled(); expect(screen.getByText('当前记账币种：人民币 / CNY')).toBeInTheDocument();
    expect(screen.getByLabelText('开始日期')).toHaveValue('2026-09-18');
    fireEvent.change(screen.getByLabelText('研究标的'), { target: { value: 'NVDA|nvda-real' } });
    fireEvent.change(screen.getByLabelText('开始日期'), { target: { value: '2025-09-15' } });
    expect(start).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '核验数据（免费）' }));
    await waitFor(() => expect(jevConvertibleTApi.availability).toHaveBeenCalledWith(expect.objectContaining({ datasetId: 'nvda-real', startDate: '2025-09-15' }), expect.any(AbortSignal)));
    expect(jevConvertibleTApi.start).not.toHaveBeenCalled();
  });

  it('replays only visible event volume and trades, and never labels incremental equity as realized', async () => {
    vi.mocked(jevConvertibleTApi.task).mockResolvedValue({ ...task(), usage: { ...task().usage, costIsEstimate: true } });
    render(<Page url="/jev-backtest?lab=convertible-t&task=event-task" />);
    expect(await screen.findByRole('heading', { name: 'NVDA · 历史事件回放' })).toBeInTheDocument();
    expect(screen.getByText(/未核验预算估值/)).toHaveTextContent('未核验预算估值 $0.0004');
    expect(document.body.textContent).not.toContain('987,654,321');
    expect(screen.queryByText('后续事件成交')).not.toBeInTheDocument();
    expect(screen.queryByText(/已实现盈亏/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole('slider', { name: '秒级研究回放进度' }), { target: { value: '2' } });
    expect(document.body.textContent).toContain('987,654,321');
    expect(screen.getByText('后续事件成交')).toBeInTheDocument();
    expect(screen.getByText('后续事件决策')).toBeInTheDocument();
    expect(screen.getByText(/不等同于已实现利润/)).toBeInTheDocument();
  });

  it('keeps later ticks at the same timestamp hidden until their own event index', async () => {
    const duplicate = task();
    duplicate.result!.equity[1].timestamp = first;
    duplicate.result!.events[1].timestamp = first;
    duplicate.result!.trades[0].timestamp = first;
    duplicate.result!.signals[0].timestamp = first;
    vi.mocked(jevConvertibleTApi.task).mockResolvedValue(duplicate);
    render(<Page url="/jev-backtest?lab=convertible-t&task=event-task" />);
    await screen.findByRole('heading', { name: 'NVDA · 历史事件回放' });
    expect(document.body.textContent).not.toContain('987,654,321');
    expect(screen.queryByText('后续事件成交')).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole('slider', { name: '秒级研究回放进度' }), { target: { value: '2' } });
    expect(document.body.textContent).toContain('987,654,321');
    expect(screen.getByText('后续事件成交')).toBeInTheDocument();
  });

  it('cancels a running task, aborts its read, and preserves only its labeled partial preview', async () => {
    const running = { ...task('running'), preview: task().result };
    vi.mocked(jevConvertibleTApi.task).mockResolvedValue(running);
    vi.mocked(jevConvertibleTApi.cancel).mockResolvedValue({ ...running, status: 'cancelled' });
    render(<Page url="/jev-backtest?lab=convertible-t&task=event-task" />);
    fireEvent.click(await screen.findByRole('button', { name: '取消实验任务' }));
    await waitFor(() => expect(jevConvertibleTApi.cancel).toHaveBeenCalledWith('event-task', expect.any(AbortSignal)));
    expect(vi.mocked(jevConvertibleTApi.task).mock.calls[0][1]?.aborted).toBe(true);
    expect(screen.getByText(/仅展示已处理的部分历史数据/)).toBeInTheDocument();
    expect(jevConvertibleTApi.start).not.toHaveBeenCalled();
  });

  it('places selected results before configuration and labels verified volume units', async () => {
    const translated = task();
    translated.result!.dataQuality = { warnings: ['Recorded order books and tape do not reproduce queue position, hidden liquidity or market impact.', 'Unrecognized future provider warning.'] };
    translated.result!.methodology = ['No shorting or borrowing. Existing base inventory can be sold and repurchased on the same day.'];
    vi.mocked(jevConvertibleTApi.task).mockResolvedValue(translated);
    render(<Page url="/jev-backtest?lab=convertible-t&task=event-task" />);
    const resultHeading = await screen.findByRole('heading', { name: 'NVDA · 历史事件回放' });
    expect(resultHeading.compareDocumentPosition(screen.getByRole('heading', { name: '实验条件' })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText('成交量（股）')).toBeInTheDocument();
    expect(screen.getByText('历史盘口和成交记录无法还原排队位置、隐藏流动性或市场冲击。')).toBeInTheDocument();
    expect(screen.getByText('不做空、不借款；已有底仓可在同日卖出和回补。')).toBeInTheDocument();
    expect(screen.getByText('Unrecognized future provider warning.')).toBeInTheDocument();
  });

  it('refreshes only research GETs every 7.5 seconds while running and stops at completion', async () => {
    vi.useFakeTimers();
    vi.mocked(jevConvertibleTApi.research).mockResolvedValueOnce({ ...research, status: 'running' }).mockResolvedValue({ ...research, status: 'completed', rounds: [{ id: 'new-round', name: '后台新完成轮次', factors: ['ohlcv'], split: '留出', period: '2025-09-16', symbol: 'NVDA', currency: 'USD', incrementalTPnl: -3 }] });
    await act(async () => { render(<Page />); });
    expect(jevConvertibleTApi.research).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(7500); });
    expect(screen.getByText('后台新完成轮次')).toBeInTheDocument();
    expect(jevConvertibleTApi.research).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(22500); });
    expect(jevConvertibleTApi.research).toHaveBeenCalledTimes(2);
    expect(jevConvertibleTApi.start).not.toHaveBeenCalled();
    expect(jevConvertibleTApi.capabilities).toHaveBeenCalledTimes(1);
  });

  it('moves a long replay to event 900 without revealing later volumes or fills', async () => {
    const long = task();
    long.result!.equity = Array.from({ length: 903 }, (_, index) => ({ ...long.result!.equity[0], eventIndex: index, timestamp: new Date(Date.UTC(2025, 8, 16, 13, 30, index)).toISOString(), totalPnl: index, incrementalTPnl: -index }));
    long.result!.events = long.result!.equity.map(point => ({ timestamp: point.timestamp, eventIndex: point.eventIndex, price: 200, volume: point.eventIndex === 900 ? 876543210 : 100 }));
    long.result!.trades = [{ ...long.result!.trades[0], eventIndex: 900, timestamp: long.result!.equity[900].timestamp }];
    long.result!.signals = [];
    vi.mocked(jevConvertibleTApi.task).mockResolvedValue(long);
    render(<Page url="/jev-backtest?lab=convertible-t&task=event-task" />);
    const slider = await screen.findByRole('slider', { name: '秒级研究回放进度' });
    fireEvent.change(slider, { target: { value: '900' } });
    await waitFor(() => expect(screen.getByText('已知事件 900 / 903')).toBeInTheDocument());
    expect(document.body.textContent).not.toContain('876,543,210');
    expect(screen.queryByText('后续事件成交')).not.toBeInTheDocument();
    fireEvent.change(slider, { target: { value: '901' } });
    await waitFor(() => expect(screen.getByText('后续事件成交')).toBeInTheDocument());
    expect(document.body.textContent).toContain('876,543,210');
  });

  it('opens published rounds through the read-only sample API without adding them to private history', async () => {
    vi.mocked(jevConvertibleTApi.research).mockResolvedValue({ ...research, status: 'completed', rounds: [{ id: 'public', name: '公开历史对照', factors: ['ohlcv'], split: '留出', period: '2025-09-16', symbol: 'NVDA', currency: 'USD', taskId: 'event-task' }] });
    render(<Page />);
    fireEvent.click(await screen.findByRole('button', { name: '查看公开轮次' }));
    await screen.findByRole('heading', { name: 'NVDA · 历史事件回放' });
    expect(jevConvertibleTApi.researchRound).toHaveBeenCalledWith('event-task', expect.any(AbortSignal));
    expect(jevConvertibleTApi.task).not.toHaveBeenCalled();
    expect(screen.getByLabelText('研究地址')).toHaveTextContent('round=event-task');
    expect(screen.getByLabelText('研究地址')).not.toHaveTextContent('task=');
    expect(screen.getByText(/公开历史样本，仅供回放查看/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '查看实验 event-ta' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '取消实验任务' })).not.toBeInTheDocument();
    expect(jevConvertibleTApi.start).not.toHaveBeenCalled();
    expect(jevConvertibleTApi.cancel).not.toHaveBeenCalled();
  });

  it('retains the published 14:00–15:55 window when loading and resubmitting its parameters', async () => {
    // Exact zoned window from published sample fab1aa0c-61c1-4f4e-b645-938de23c1c66.
    const sample = { ...task(), visibility: 'public_sample' as const, config: { ...config, maxCostUsd: .457305278, evaluationStart: '2025-09-16T14:00:00-04:00', evaluationEnd: '2025-09-16T15:55:00-04:00' } };
    vi.mocked(jevConvertibleTApi.researchRound).mockResolvedValue(sample);
    vi.mocked(jevConvertibleTApi.start).mockResolvedValue(task('queued'));
    render(<Page url="/jev-backtest?lab=convertible-t&round=event-task" />);
    fireEvent.click(await screen.findByRole('button', { name: '载入所选实验参数' }));
    expect(screen.getByLabelText('日内研究开始（ISO8601，含时区）')).toHaveValue('2025-09-16T14:00:00-04:00');
    expect(screen.getByLabelText('日内研究结束（ISO8601，含时区）')).toHaveValue('2025-09-16T15:55:00-04:00');
    fireEvent.click(screen.getByRole('button', { name: '开始秒级回测（付费）' }));
    await waitFor(() => expect(jevConvertibleTApi.start).toHaveBeenCalledWith(expect.objectContaining({ maxCostUsd: .457305278, evaluationStart: '2025-09-16T14:00:00-04:00', evaluationEnd: '2025-09-16T15:55:00-04:00' }), expect.any(String), expect.any(AbortSignal)));
  });

  it('blocks partial, unzoned, reversed or out-of-date windows before a paid POST', async () => {
    render(<Page />);
    const start = await screen.findByRole('button', { name: '开始秒级回测（付费）' });
    await waitFor(() => expect(start).toBeEnabled());
    const begin = screen.getByLabelText('日内研究开始（ISO8601，含时区）');
    const end = screen.getByLabelText('日内研究结束（ISO8601，含时区）');
    fireEvent.change(begin, { target: { value: '2025-09-16T14:00:00-04:00' } });
    fireEvent.click(start); expect(screen.getByRole('alert')).toHaveTextContent('同时填写');
    fireEvent.change(end, { target: { value: '2025-09-16T15:55:00' } });
    fireEvent.click(start); expect(screen.getByRole('alert')).toHaveTextContent('含时区的 ISO8601');
    fireEvent.change(end, { target: { value: '2025-09-16T13:00:00-04:00' } });
    fireEvent.click(start); expect(screen.getByRole('alert')).toHaveTextContent('须晚于');
    fireEvent.change(end, { target: { value: '2025-09-17T15:55:00-04:00' } });
    fireEvent.click(start); expect(screen.getByRole('alert')).toHaveTextContent('数据日期区间内');
    expect(jevConvertibleTApi.start).not.toHaveBeenCalled();
  });

  it('keeps private task deep links on the owner-scoped API even if the same task is a published round', async () => {
    render(<Page url="/jev-backtest?lab=convertible-t&task=event-task" />);
    await screen.findByRole('heading', { name: 'NVDA · 历史事件回放' });
    expect(jevConvertibleTApi.task).toHaveBeenCalledWith('event-task', expect.any(AbortSignal));
    expect(jevConvertibleTApi.researchRound).not.toHaveBeenCalled();
    expect(screen.queryByText(/公开历史样本，仅供回放查看/)).not.toBeInTheDocument();
  });

  it('rejects a sample response that lacks its explicit public marker', async () => {
    vi.mocked(jevConvertibleTApi.researchRound).mockResolvedValue(task());
    render(<Page url="/jev-backtest?lab=convertible-t&round=event-task" />);
    expect(await screen.findByText(/返回的任务编号或公开属性与所选记录不一致/)).toBeInTheDocument();
    expect(screen.queryByRole('slider')).not.toBeInTheDocument();
    expect(jevConvertibleTApi.task).not.toHaveBeenCalled();
  });

  it('shows real research rounds in returned order including losing rounds', async () => {
    vi.mocked(jevConvertibleTApi.research).mockResolvedValue({ ...research, status: 'completed', rounds: [{ id: 'loss', name: '第一轮亏损', factors: ['ohlcv'], split: '留出测试', period: '2025-09-16 下午', symbol: 'NVDA', currency: 'USD', incrementalTPnl: -125, totalPnl: -85, tradeCount: 4 }, { id: 'gain', name: '第二轮改善', factors: ['ohlcv', 'orderbook'], split: '验证区间', period: '2025-09-16 上午', symbol: 'NVDA', currency: 'USD', incrementalTPnl: 5, totalPnl: 30, tradeCount: 2 }] });
    render(<Page />);
    const rows = within(await screen.findByRole('region', { name: '实测研究轮次' })).getAllByRole('row');
    expect(rows[1]).toHaveTextContent('第一轮亏损'); expect(rows[1]).toHaveTextContent('-125.00 USD');
    expect(rows[2]).toHaveTextContent('第二轮改善'); expect(jevConvertibleTApi.start).not.toHaveBeenCalled();
  });

  it('clears private snapshots and research after a 401 response', async () => {
    render(<Page url="/jev-backtest?lab=convertible-t&task=event-task" />);
    await screen.findByRole('heading', { name: 'NVDA · 历史事件回放' });
    vi.mocked(jevConvertibleTApi.tasks).mockRejectedValue({ response: { status: 401, data: { detail: 'Expired' } } });
    fireEvent.click(screen.getByRole('button', { name: '刷新实验历史' }));
    expect(await screen.findByText(/当前账号的实验任务和结果已清除/)).toBeInTheDocument();
    expect(screen.queryByRole('slider')).not.toBeInTheDocument();
    expect(screen.queryByText('多轮真实对照')).not.toBeInTheDocument();
    expect(jevConvertibleTApi.start).not.toHaveBeenCalled();
  });

  it('switches labs without routing the previous lab task into the other API', async () => {
    vi.mocked(jevBacktestApi.capabilities).mockResolvedValue({ configured: false, model: 'test-model', provider: 'test', timeframe: '1min', limits: { maxCostUsd: .5, maxEvaluationBars: 2000 }, defaults: {}, dataSources: [] });
    vi.mocked(jevBacktestApi.tasks).mockResolvedValue({ items: [] });
    render(<Page tabs url="/jev-backtest?lab=convertible-t&task=event-task" />);
    await screen.findByRole('heading', { name: 'NVDA · 历史事件回放' });
    fireEvent.click(screen.getByRole('button', { name: '分钟行情回测' }));
    await screen.findByRole('button', { name: '生成信号并回测' });
    expect(screen.getByLabelText('研究地址')).toHaveTextContent('');
    expect(jevBacktestApi.task).not.toHaveBeenCalled();
    expect(jevBacktestApi.start).not.toHaveBeenCalled();
  });
});
