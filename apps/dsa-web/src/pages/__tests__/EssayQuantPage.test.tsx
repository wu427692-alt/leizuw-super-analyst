import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { essayQuantApi } from '../../api/essayQuant';
import EssayQuantPage from '../EssayQuantPage';

vi.mock('../../api/essayQuant', () => ({
  essayQuantApi: {
    dashboard: vi.fn(), catalog: vi.fn(), runs: vi.fn(), rules: vi.fn(), precomputeStatus: vi.fn(),
    run: vi.fn(), saveRule: vi.fn(), plan: vi.fn(), executePlan: vi.fn(),
  },
}));

const history = {
  total: 1,
  items: [{
    id: 55, name: '看多小作文事件驱动策略回测', strategyType: 'essay_event',
    eventCount: 4041, matureEventCount: 3744, primaryAverageExcess: -1.98,
    confidenceInterval: [-2.35, -1.67] as [number, number], priceCutoff: '2026-08-21',
    createdAt: '2026-08-22T01:48:34',
  }],
};

describe('EssayQuantPage section-scoped loading', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(essayQuantApi.runs).mockResolvedValue(history);
    vi.mocked(essayQuantApi.precomputeStatus).mockRejectedValue(new Error('timeout of 30000ms exceeded'));
  });

  it('loads history without requesting unrelated heavy modules or showing their timeout', async () => {
    render(<MemoryRouter initialEntries={['/essay-quant?section=history']}><EssayQuantPage /></MemoryRouter>);

    expect(await screen.findByRole('heading', { name: '运行历史' })).toBeInTheDocument();
    expect(await screen.findByText('看多小作文事件驱动策略回测')).toBeInTheDocument();
    expect(screen.getByText('历史行情截止 2026-08-21')).toBeInTheDocument();
    expect(screen.queryByText(/连接上游服务超时/)).not.toBeInTheDocument();
    expect(screen.queryByText(/后台模块尚未就绪/)).not.toBeInTheDocument();
    expect(essayQuantApi.dashboard).not.toHaveBeenCalled();
    expect(essayQuantApi.catalog).not.toHaveBeenCalled();
    expect(essayQuantApi.rules).not.toHaveBeenCalled();
  });

  it('reports a history-specific local degradation instead of an upstream dependency claim', async () => {
    vi.mocked(essayQuantApi.runs).mockRejectedValueOnce(new Error('timeout of 30000ms exceeded'));
    render(<MemoryRouter initialEntries={['/essay-quant?section=history']}><EssayQuantPage /></MemoryRouter>);

    await waitFor(() => expect(screen.getByText(/运行历史暂时未更新/)).toBeInTheDocument());
    expect(screen.queryByText(/外部依赖/)).not.toBeInTheDocument();
  });
});
