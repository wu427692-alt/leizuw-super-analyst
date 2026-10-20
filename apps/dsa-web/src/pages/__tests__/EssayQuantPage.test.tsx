import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { essayQuantApi } from '../../api/essayQuant';
import type { EssayQuantDashboard, EssayQuantTask } from '../../types/essayQuant';
import EssayQuantPage from '../EssayQuantPage';

vi.mock('../../api/essayQuant', () => ({
  essayQuantApi: {
    dashboard: vi.fn(), catalog: vi.fn(), runs: vi.fn(), rules: vi.fn(), precomputeStatus: vi.fn(),
    tasks: vi.fn(), taskStatus: vi.fn(), runResult: vi.fn(), startTask: vi.fn(),
    run: vi.fn(), saveRule: vi.fn(), plan: vi.fn(), executePlan: vi.fn(),
  },
}));

const history = {
  total: 1,
  items: [{
    id: 55, name: '看多小作文事件驱动策略回测', strategyType: 'essay_event',
    eventCount: 4041, matureEventCount: 3744, primaryAverageExcess: -1.98, outOfSampleExcess: -1.67,
    verdict: '暂不采用', maxDrawdown: -12.4,
    confidenceInterval: [-2.35, -1.67] as [number, number], priceCutoff: '2026-08-21',
    createdAt: '2026-08-22T01:48:34',
  }],
};

describe('EssayQuantPage section-scoped loading', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(essayQuantApi.runs).mockResolvedValue(history);
    vi.mocked(essayQuantApi.tasks).mockResolvedValue({ total: 1, items: [{
      taskId: 'task-1', status: 'completed', progress: 100, message: '研究完成',
      name: '我的后台量化任务', strategyType: 'essay_event', resultRunId: 55,
      error: null, createdAt: '2026-08-22T01:40:00', startedAt: '2026-08-22T01:41:00',
      completedAt: '2026-08-22T01:48:34',
    }] });
  });

  it('loads history without requesting unrelated heavy modules or showing their timeout', async () => {
    render(<MemoryRouter initialEntries={['/essay-quant?section=history']}><EssayQuantPage /></MemoryRouter>);

    expect(await screen.findByRole('heading', { name: '已完成任务' })).toBeInTheDocument();
    expect(await screen.findByText('看多小作文事件驱动策略回测')).toBeInTheDocument();
    expect(await screen.findByText('我的后台量化任务')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '查看结果' })).toBeInTheDocument();
    expect(screen.getByText('行情截止 2026-08-21')).toBeInTheDocument();
    expect(screen.queryByText(/连接上游服务超时/)).not.toBeInTheDocument();
    expect(screen.queryByText(/后台模块尚未就绪/)).not.toBeInTheDocument();
    expect(essayQuantApi.dashboard).not.toHaveBeenCalled();
    expect(essayQuantApi.catalog).not.toHaveBeenCalled();
    expect(essayQuantApi.rules).not.toHaveBeenCalled();
  });

  it('reports a history-specific local degradation instead of an upstream dependency claim', async () => {
    vi.mocked(essayQuantApi.runs).mockRejectedValueOnce(new Error('timeout of 30000ms exceeded'));
    render(<MemoryRouter initialEntries={['/essay-quant?section=history']}><EssayQuantPage /></MemoryRouter>);

    await waitFor(() => expect(screen.getByText(/任务档案暂时未更新/)).toBeInTheDocument());
    expect(screen.queryByText(/外部依赖/)).not.toBeInTheDocument();
  });

  it('opens the workbench immediately when a heavy overview request is still pending', async () => {
    vi.mocked(essayQuantApi.dashboard).mockImplementation(() => new Promise(() => undefined));
    vi.mocked(essayQuantApi.catalog).mockResolvedValue({ generatedAt: '2026-08-25T00:00:00', assets: [], methods: [], safeguards: [] });
    vi.mocked(essayQuantApi.runs).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(essayQuantApi.tasks).mockResolvedValue({ items: [], total: 0 });

    render(<MemoryRouter initialEntries={['/essay-quant']}><EssayQuantPage /></MemoryRouter>);

    expect(screen.getByRole('heading', { name: '量化研究任务中心' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '新建研究任务' })).toBeInTheDocument();
    expect(screen.queryByText('正在装载量化研究工作台')).not.toBeInTheDocument();
  });
});

function snapshot(runId: number, name: string): EssayQuantDashboard {
  return {
    runId, snapshotHash: `snapshot-${runId}`, generatedAt: '2026-08-22T01:00:00Z',
    rule: {
      name, sourceQuery: '', signalDirection: 'bullish', lookbackDays: 30, holdingPeriods: runId === 55 ? [5] : [20],
      firstMentionOnly: false, firstMentionWindowDays: 30, minImportance: 60, minConfidence: 0.5,
      benchmarkCode: '000300.SH', portfolioSize: 10, strategyType: 'essay_event', rawNotePolicy: 'exclude',
      dedupeWindowDays: 3, transactionCostBps: 12, validationMethod: 'walk_forward',
    },
    summary: { eventCount: 5, matureEventCount: 4, coveredStockCount: 1, firstMention30dCount: 1, metrics: [], excessMetrics: [] },
    eventCurve: [], researchGroupRankings: [], firstMentions30d: [], hypeAnalysis: [], trendSignals: [],
    portfolio: { components: [], curve: [] },
    robustness: { sampleCount: 4, confidenceInterval95: [null, null], distribution: [], cohorts: [], sensitivity: [] },
    factorAnalysis: [], events: [],
    dataQuality: {
      essaySource: '研究语料', priceSource: 'Tushare', priceBasis: '前复权', priceCutoff: '2026-08-21',
      priceTargetDate: '2026-08-21', priceFreshnessRatio: 100, entryRule: '下一交易日开盘', exitRule: '持有期结束',
      benchmark: '沪深300', survivorshipNote: '历史截面', warnings: [],
    },
  };
}

function LocationProbe() {
  const location = useLocation();
  const navigate = useNavigate();
  return <><output aria-label="当前路由">{location.pathname}{location.search}</output><button onClick={() => navigate('/essay-quant?section=results&run=56')}>切换到另一个运行</button><button onClick={() => navigate('/essay-quant?section=history&task=task-b')}>切换指定任务</button></>;
}

function renderAt(url: string) {
  return render(<MemoryRouter initialEntries={[url]}><EssayQuantPage /><LocationProbe /></MemoryRouter>);
}

describe('EssayQuantPage immutable run selection', () => {
  const older = snapshot(55, '历史研究 A');
  const latest = snapshot(56, '最新研究 B');

  beforeEach(() => {
    vi.resetAllMocks();
    vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
    vi.mocked(essayQuantApi.dashboard).mockResolvedValue(latest);
    vi.mocked(essayQuantApi.catalog).mockResolvedValue({ generatedAt: '2026-08-25', assets: [], methods: [], safeguards: [] });
    vi.mocked(essayQuantApi.runs).mockResolvedValue(history);
    vi.mocked(essayQuantApi.tasks).mockResolvedValue({ total: 1, items: [{
      taskId: 'task-1', status: 'completed', progress: 100, message: '已完成',
      name: '旧运行任务', strategyType: 'essay_event', resultRunId: 55,
    }] });
    vi.mocked(essayQuantApi.runResult).mockImplementation(async runId => runId === 55 ? older : latest);
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it('opens the selected history run and records its ID in the URL without loading the latest dashboard', async () => {
    renderAt('/essay-quant?section=history');
    fireEvent.click(await screen.findByRole('button', { name: '查看结果' }));
    expect(await screen.findByText('历史研究 A')).toBeInTheDocument();
    expect(screen.getByLabelText('当前路由')).toHaveTextContent('/essay-quant?section=results&run=55');
    expect(screen.getByText('主观察窗 5 日，样本 4')).toBeInTheDocument();
    expect(screen.queryByText('最新研究 B')).not.toBeInTheDocument();
    expect(essayQuantApi.dashboard).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '结果研判' }));
    expect(screen.getByLabelText('当前路由')).toHaveTextContent('run=55');
  });

  it('does not let an older in-flight dashboard response overwrite the selected result', async () => {
    let resolveLatest!: (value: EssayQuantDashboard) => void;
    vi.mocked(essayQuantApi.dashboard).mockImplementation(() => new Promise(resolve => { resolveLatest = resolve; }));
    renderAt('/essay-quant');
    fireEvent.click(screen.getByRole('button', { name: '任务档案' }));
    fireEvent.click(await screen.findByRole('button', { name: '查看结果' }));
    expect(await screen.findByText('历史研究 A')).toBeInTheDocument();
    await act(async () => { resolveLatest(latest); });
    expect(screen.getByText('历史研究 A')).toBeInTheDocument();
    expect(screen.queryByText('最新研究 B')).not.toBeInTheDocument();
    expect(screen.getByText('主观察窗 5 日，样本 4')).toBeInTheDocument();
  });

  it('reopens the same immutable run from a copied URL or full page reload', async () => {
    const first = renderAt('/essay-quant?section=results&run=55');
    expect(await screen.findByText('历史研究 A')).toBeInTheDocument();
    first.unmount();
    renderAt('/essay-quant?section=results&run=55');
    expect(await screen.findByText('历史研究 A')).toBeInTheDocument();
    expect(essayQuantApi.runResult).toHaveBeenCalledTimes(2);
    expect(essayQuantApi.runResult).toHaveBeenLastCalledWith(55);
    expect(essayQuantApi.dashboard).not.toHaveBeenCalled();
  });

  it('ignores a late response from the previously selected run', async () => {
    let resolveOlder!: (value: EssayQuantDashboard) => void;
    vi.mocked(essayQuantApi.runResult).mockImplementation(runId => runId === 55
      ? new Promise(resolve => { resolveOlder = resolve; }) : Promise.resolve(latest));
    renderAt('/essay-quant?section=results&run=55');
    fireEvent.click(screen.getByRole('button', { name: '切换到另一个运行' }));
    expect(await screen.findByText('最新研究 B')).toBeInTheDocument();
    await act(async () => { resolveOlder(older); });
    expect(screen.queryByText('历史研究 A')).not.toBeInTheDocument();
    expect(screen.getByText('最新研究 B')).toBeInTheDocument();
    expect(screen.getByLabelText('当前路由')).toHaveTextContent('run=56');
  });

  it('keeps the requested ID on failure and retries that run instead of substituting the latest result', async () => {
    vi.mocked(essayQuantApi.runResult).mockRejectedValueOnce(new Error('读取超时')).mockResolvedValue(older);
    renderAt('/essay-quant?section=results&run=55');
    expect(await screen.findByText('运行 #55 暂时无法读取')).toBeInTheDocument();
    expect(screen.queryByText('最新研究 B')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试读取此运行' }));
    expect(await screen.findByText('历史研究 A')).toBeInTheDocument();
    expect(essayQuantApi.runResult).toHaveBeenNthCalledWith(2, 55);
    expect(essayQuantApi.dashboard).not.toHaveBeenCalled();
  });

  it('rejects a response for a different run instead of presenting it as the requested snapshot', async () => {
    vi.mocked(essayQuantApi.runResult).mockResolvedValue(latest);
    renderAt('/essay-quant?section=results&run=55');
    expect(await screen.findByText('返回的运行编号与所选记录不一致，请重试读取。')).toBeInTheDocument();
    expect(screen.queryByText('最新研究 B')).not.toBeInTheDocument();
  });

  it('does not silently substitute a latest result when the URL has an invalid run ID', () => {
    renderAt('/essay-quant?section=results&run=invalid');
    expect(screen.getByText('运行编号无效')).toBeInTheDocument();
    expect(essayQuantApi.runResult).not.toHaveBeenCalled();
    expect(essayQuantApi.dashboard).not.toHaveBeenCalled();
  });
});

describe('EssayQuantPage 指定后台任务定位', () => {
  const target: EssayQuantTask = {
    taskId: 'old-target', name: '不在最近列表内的任务', strategyType: 'essay_event',
    status: 'running', progress: 45, message: '目标任务计算中',
  };
  beforeEach(() => {
    vi.useFakeTimers(); vi.resetAllMocks();
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
    vi.mocked(essayQuantApi.tasks).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(essayQuantApi.runs).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(essayQuantApi.taskStatus).mockResolvedValue(target);
  });
  afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });
  async function flush() { await act(async () => { await Promise.resolve(); }); }
  async function advance(ms: number) { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); }

  it('直接读取列表之外的指定任务；短暂失败保留进度，完成后可进入准确快照', async () => {
    vi.mocked(essayQuantApi.taskStatus).mockResolvedValueOnce(target).mockRejectedValueOnce(new Error('503'))
      .mockResolvedValue({ ...target, status: 'completed', progress: 100, message: '目标完成', resultRunId: 55 });
    vi.mocked(essayQuantApi.runResult).mockResolvedValue(snapshot(55, '目标任务结果'));
    renderAt('/essay-quant?section=history&task=old-target'); await flush();
    const selected = within(screen.getByRole('region', { name: '指定后台任务' }));
    expect(selected.getByText(target.name)).toBeInTheDocument();
    expect(essayQuantApi.taskStatus).toHaveBeenCalledWith('old-target');
    expect(essayQuantApi.dashboard).not.toHaveBeenCalled();
    await advance(3_000);
    expect(selected.getByText('45%')).toBeInTheDocument();
    expect(selected.getByText(/任务状态暂时未更新/)).toBeInTheDocument();
    await advance(5_000);
    expect(selected.getByText('100%')).toBeInTheDocument();
    await advance(20_000);
    expect(essayQuantApi.taskStatus).toHaveBeenCalledTimes(3);
    fireEvent.click(selected.getByRole('button', { name: '查看此任务结果' })); await flush();
    expect(screen.getByText('目标任务结果')).toBeInTheDocument();
    expect(screen.getByLabelText('当前路由')).toHaveTextContent('section=results&run=55');
  });

  it('切换指定任务后，上一任务慢响应不能串到新任务', async () => {
    let finish!: (value: EssayQuantTask) => void;
    vi.mocked(essayQuantApi.taskStatus).mockImplementation(id => id === 'old-target'
      ? new Promise(resolve => { finish = resolve; })
      : Promise.resolve({ ...target, taskId: 'task-b', name: '任务 B' }));
    renderAt('/essay-quant?section=history&task=old-target'); await flush();
    fireEvent.click(screen.getByRole('button', { name: '切换指定任务' })); await flush();
    expect(screen.getByText('任务 B')).toBeInTheDocument();
    await act(async () => { finish(target); });
    expect(screen.queryByText(target.name)).not.toBeInTheDocument();
    expect(screen.getByText('任务 B')).toBeInTheDocument();
  });

  it.each([401, 403, 404, 410])('接口明确 %s 时隐藏旧任务并停止轮询，允许手动重试', async status => {
    vi.mocked(essayQuantApi.taskStatus).mockResolvedValueOnce(target)
      .mockRejectedValueOnce({ isAxiosError: true, response: { status } }).mockResolvedValue(target);
    renderAt('/essay-quant?section=history&task=old-target'); await flush(); await advance(3_000);
    expect(screen.queryByText(target.name)).not.toBeInTheDocument();
    expect(screen.getByText(/该任务不存在或当前账号无权查看/)).toBeInTheDocument();
    await advance(20_000); expect(essayQuantApi.taskStatus).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole('button', { name: '重试指定任务' })); await flush();
    expect(screen.getByText(target.name)).toBeInTheDocument();
  });

  it('页面隐藏暂停读取，重新可见后恢复，不重叠慢请求', async () => {
    let finish!: (value: EssayQuantTask) => void;
    vi.mocked(essayQuantApi.taskStatus).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }))
      .mockResolvedValue(target);
    renderAt('/essay-quant?section=history&task=old-target'); await flush(); await advance(9_000);
    expect(essayQuantApi.taskStatus).toHaveBeenCalledTimes(1);
    await act(async () => { finish(target); });
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    await advance(9_000); expect(essayQuantApi.taskStatus).toHaveBeenCalledTimes(1);
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    await advance(3_000); expect(essayQuantApi.taskStatus).toHaveBeenCalledTimes(2);
  });
});
