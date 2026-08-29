import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { essayQuantApi } from '../../api/essayQuant';
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
